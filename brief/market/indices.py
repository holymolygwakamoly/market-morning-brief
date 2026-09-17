"""지수·환율·금리·원자재·ETF 카드 시세 — Yahoo 차트 일봉에서 직전 완료 세션을 고른다 (PLAN §11.2)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any

from brief.market.base import IndexQuote
from brief.market.yahoo import YahooClient, YahooError, parse_bars, pick_session

logger = logging.getLogger(__name__)

HISTORY_BARS = 6


def build_index_quote(cfg: dict, result: dict, run_date: date) -> IndexQuote:
    """chart result → IndexQuote. 직전 완료 세션(현지 날짜 < run_date)의 종가·전일 대비."""
    bars, _vols, _tz = parse_bars(result)
    meta = result.get("meta") or {}
    idx = pick_session(bars, run_date)
    if idx is None:
        raise YahooError(f"{cfg['symbol']}: run_date 이전 세션 봉이 없음 (bars={len(bars)})")
    bar = bars[idx]
    prev = bars[idx - 1].close if idx >= 1 else None
    if prev:
        change_pct = round((bar.close / prev - 1) * 100, 2)
        change_abs = round(bar.close - prev, 4)
    elif idx == len(bars) - 1 and meta.get("regularMarketChangePercent") is not None:
        change_pct = round(float(meta["regularMarketChangePercent"]), 2)
        change_abs = None
    else:
        change_pct, change_abs = 0.0, None
    # asof: 선택한 봉이 마지막 봉이면 regularMarketTime, 아니면 그 봉의 현지 날짜 자정(UTC)
    if idx == len(bars) - 1 and meta.get("regularMarketTime"):
        asof = datetime.fromtimestamp(int(meta["regularMarketTime"]), tz=timezone.utc)
    else:
        asof = datetime(bar.date.year, bar.date.month, bar.date.day, tzinfo=timezone.utc)
    return IndexQuote(
        symbol=cfg["symbol"],
        name=cfg["name"],
        region=cfg.get("region", "MACRO"),
        kind=cfg.get("kind", "index"),
        close=bar.close,
        prev_close=prev,
        change_pct=change_pct,
        change_abs=change_abs,
        session_date=bar.date,
        asof=asof,
        currency=meta.get("currency"),
        history=bars[max(0, idx - HISTORY_BARS + 1): idx + 1],
        constituents_key=cfg.get("constituents"),
        group=cfg.get("group"),
    )


def fetch_indices(
    symbols_cfg: list[dict],
    run_date: date,
    *,
    yahoo: YahooClient,
    max_workers: int = 8,
) -> tuple[list[IndexQuote], list[str]]:
    """심볼별 독립 예외 격리. 반환 순서는 설정 순서."""
    errors: list[str] = []

    def one(cfg: dict) -> IndexQuote | None:
        try:
            return build_index_quote(cfg, yahoo.chart(cfg["symbol"]), run_date)
        except Exception as e:  # noqa: BLE001
            logger.warning("index %s failed: %s", cfg.get("symbol"), e)
            errors.append(f"{cfg.get('symbol')}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        results: list[Any] = list(ex.map(one, symbols_cfg))
    return [q for q in results if q is not None], errors
