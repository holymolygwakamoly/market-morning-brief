"""시장 데이터 계층 진입점 — `collect_market(cfg, run_date)` → MarketSnapshot.

cfg = sources.yaml 의 `market` 섹션: `indices: [{symbol, name, region, kind, group, constituents?}]`,
`constituents: [us_sp500, ...]`, `cache_dir`.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from brief.market.base import ConstituentTable, IndexQuote, InvestorFlow, MarketSnapshot
from brief.market.constituents import fetch_constituents
from brief.market.indices import fetch_indices
from brief.market.yahoo import YahooClient

__all__ = ["collect_market", "MarketSnapshot", "IndexQuote", "ConstituentTable", "InvestorFlow"]

logger = logging.getLogger(__name__)


def collect_market(
    market_cfg: dict,
    run_date: date,
    *,
    cache_dir: Path,
    yahoo: YahooClient | None = None,
    with_constituents: bool = True,
    budget_s: float = 300.0,
) -> MarketSnapshot:
    t0 = time.monotonic()
    snap = MarketSnapshot(run_date=run_date)
    owns = yahoo is None
    y = yahoo or YahooClient()
    try:
        keys = list(market_cfg.get("constituents") or []) if with_constituents else []
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_idx = ex.submit(fetch_indices, market_cfg.get("indices") or [], run_date, yahoo=y)
            f_con = ex.submit(fetch_constituents, keys, run_date, yahoo=y, cache_dir=cache_dir, budget_s=budget_s) if keys else None
            indices, errors = f_idx.result()
            snap.indices = indices
            snap.errors.extend(errors)
            if f_con is not None:
                snap.constituents = f_con.result()
                for t in snap.constituents.values():
                    if t.error:
                        snap.errors.append(f"{t.key}: {t.error}")
    finally:
        if owns:
            y.close()
    snap.elapsed_s = round(time.monotonic() - t0, 1)
    logger.info(
        "market: indices %d, constituents %s, errors %d, %.1fs",
        len(snap.indices), {k: len(t.rows) for k, t in snap.constituents.items()}, len(snap.errors), snap.elapsed_s,
    )
    return snap
