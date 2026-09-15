"""Yahoo Finance chart API에서 시세(Quote)를 가져온다."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from .base import Quote
from .http import fetch_text

logger = logging.getLogger(__name__)


def daily_change(meta: dict, result: dict) -> tuple[float | None, float]:
    """일간 등락률. `chartPreviousClose`는 차트 범위(5d) 시작 전 종가라 쓰지 않는다.

    우선순위: meta.regularMarketChangePercent → 일봉 close 배열의 마지막 두 유효값 → 0.0
    """
    close = float(meta["regularMarketPrice"])
    closes = [c for c in (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or [] if c is not None]
    if meta.get("regularMarketChangePercent") is not None:
        pct = float(meta["regularMarketChangePercent"])
        prev = round(close / (1 + pct / 100), 4) if pct != -100 else None
        return prev, round(pct, 2)
    if len(closes) >= 2:
        prev = float(closes[-2]) if abs(float(closes[-1]) - close) < 1e-9 else float(closes[-1])
        return prev, round((close / prev - 1) * 100, 2)
    return None, 0.0


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
            prev_close, change_pct = daily_change(meta, result)
            asof = datetime.fromtimestamp(int(meta["regularMarketTime"]), tz=timezone.utc)
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
