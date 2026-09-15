"""Yahoo Finance chart API에서 시세(Quote)를 가져온다."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from .base import Quote
from .http import fetch_text

logger = logging.getLogger(__name__)


def fetch_quotes(
    quotes_cfg: dict,
    defaults: dict,
    client: httpx.Client | None = None,
) -> tuple[list[Quote], list[str]]:
    """심볼별로 독립 예외 격리하며 Quote 목록을 구성한다."""
    timeout_s = quotes_cfg.get("timeout_s", defaults.get("timeout_s", 15))
    retries = quotes_cfg.get("retries", defaults.get("retries", 2))
    user_agent = defaults.get("user_agent", "MarketMorningBrief/0.1")
    url_template = quotes_cfg["url_template"]

    quotes: list[Quote] = []
    errors: list[str] = []
    for sym_cfg in quotes_cfg.get("symbols", []):
        symbol = sym_cfg["symbol"]
        name = sym_cfg["name"]
        try:
            url = url_template.format(symbol=symbol)
            text = fetch_text(url, timeout_s=timeout_s, retries=retries, user_agent=user_agent, client=client)
            data = json.loads(text)
            result = data["chart"]["result"][0]
            meta = result["meta"]
            close = float(meta["regularMarketPrice"])
            prev_close_raw = meta.get("chartPreviousClose", meta.get("previousClose"))
            prev_close = float(prev_close_raw) if prev_close_raw is not None else None
            asof = datetime.fromtimestamp(int(meta["regularMarketTime"]), tz=timezone.utc)
            change_pct = round((close / prev_close - 1) * 100, 2) if prev_close else 0.0
            quotes.append(
                Quote(
                    symbol=symbol,
                    name=name,
                    close=close,
                    change_pct=change_pct,
                    asof=asof,
                    prev_close=prev_close,
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("yahoo_chart failed symbol=%s err=%s", symbol, e)
            errors.append(f"{symbol}: {e}")
    return quotes, errors
