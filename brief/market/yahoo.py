"""Yahoo Finance JSON 클라이언트 — 차트(일봉)와 배치 quote(v7, crumb).

- chart: `v8/finance/chart/{symbol}?range=1mo&interval=1d` → 일봉 목록. 직전 완료 세션 선택은 `pick_session`.
- quotes: `v7/finance/quote?symbols=a,b,...&crumb=…` — 한 요청에 최대 `BATCH` 심볼(시총·거래량·등락률 포함).
  crumb은 `fc.yahoo.com`(쿠키) → `v1/test/getcrumb` 순으로 얻는다(공개 웹 프론트가 쓰는 흐름, yfinance와 동일).
- 요청 수는 하루 지수 ≈45회 + 배치 ≈15회로 제한한다(README §4 소스 정책).
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from brief.market.base import Bar

logger = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"
CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
COOKIE_URL = "https://fc.yahoo.com"
BATCH = 250
QUOTE_FIELDS = (
    "symbol,shortName,longName,regularMarketPrice,regularMarketChangePercent,regularMarketChange,"
    "regularMarketVolume,regularMarketPreviousClose,marketCap,currency,regularMarketTime,marketState,exchange"
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


class YahooError(Exception):
    pass


def parse_bars(result: dict) -> tuple[list[Bar], list[float | None], str]:
    """chart result → (일봉 Bar 목록(현지 날짜), 거래량 목록, 거래소 tz 이름). null 종가 봉은 제외."""
    meta = result.get("meta") or {}
    tzname = meta.get("exchangeTimezoneName") or "UTC"
    try:
        tz = ZoneInfo(tzname)
    except Exception:  # noqa: BLE001
        tz = timezone.utc
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    bars: list[Bar] = []
    vols: list[float | None] = []
    for i, ts in enumerate(stamps):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        d = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(tz).date()
        if bars and bars[-1].date == d:  # 같은 날 봉 중복(장중 갱신) → 마지막 값 유지
            bars[-1] = Bar(date=d, close=float(c))
            vols[-1] = volumes[i] if i < len(volumes) else None
            continue
        bars.append(Bar(date=d, close=float(c)))
        vols.append(volumes[i] if i < len(volumes) else None)
    return bars, vols, tzname


def pick_session(bars: list[Bar], run_date: date) -> int | None:
    """직전 완료 세션의 인덱스: 현지 날짜 < run_date 인 마지막 봉. 없으면 None."""
    for i in range(len(bars) - 1, -1, -1):
        if bars[i].date < run_date:
            return i
    return None


class YahooClient:
    def __init__(self, client: httpx.Client | None = None, *, timeout_s: float = 15.0, retries: int = 1,
                 backoff_s: float = 2.0, user_agent: str = BROWSER_UA, sleep=time.sleep) -> None:
        self._owns = client is None
        self.client = client or httpx.Client(follow_redirects=True, headers={"User-Agent": user_agent, "Accept": "*/*"})
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self.sleep = sleep
        self._crumb: str | None = None

    def close(self) -> None:
        if self._owns:
            self.client.close()

    # -- 저수준
    def _get_json(self, url: str, params: dict | None = None, *, timeout_s: float | None = None) -> Any:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.client.get(url, params=params, timeout=timeout_s or self.timeout_s)
            except httpx.TransportError as e:
                last = e
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = YahooError(f"status {resp.status_code}")
                elif resp.status_code >= 400:
                    raise YahooError(f"status {resp.status_code}: {resp.text[:120]}")
                else:
                    return resp.json()
            if attempt < self.retries:
                self.sleep(self.backoff_s * (attempt + 1) * (30 if isinstance(last, YahooError) and "429" in str(last) else 1))
        raise YahooError(str(last))

    # -- 차트
    def chart(self, symbol: str, *, range_: str = "1mo", interval: str = "1d") -> dict:
        data = self._get_json(CHART_URL.format(symbol=symbol), {"range": range_, "interval": interval})
        chart = data.get("chart") or {}
        if chart.get("error"):
            raise YahooError(str(chart["error"].get("description") or chart["error"]))
        results = chart.get("result") or []
        if not results:
            raise YahooError("empty chart result")
        return results[0]

    # -- 배치 quote
    def _ensure_crumb(self) -> str:
        if self._crumb:
            return self._crumb
        try:
            self.client.get(COOKIE_URL, timeout=self.timeout_s)  # 404이지만 쿠키(A3)가 설정된다
        except httpx.TransportError as e:
            raise YahooError(f"cookie: {e}") from e
        resp = self.client.get(CRUMB_URL, timeout=self.timeout_s)
        crumb = resp.text.strip()
        if resp.status_code != 200 or not crumb or "<" in crumb:
            raise YahooError(f"crumb 실패: status {resp.status_code} {crumb[:60]!r}")
        self._crumb = crumb
        return crumb

    def quotes(self, symbols: list[str], *, budget_s: float | None = None) -> dict[str, dict]:
        """심볼 → v7 quote dict. 배치 단위 예외 격리(실패 배치는 건너뜀, 로그)."""
        out: dict[str, dict] = {}
        if not symbols:
            return out
        t0 = time.monotonic()
        crumb = self._ensure_crumb()
        for i in range(0, len(symbols), BATCH):
            if budget_s is not None and time.monotonic() - t0 > budget_s:
                logger.warning("yahoo quotes: budget exceeded after %d/%d symbols", i, len(symbols))
                break
            batch = symbols[i:i + BATCH]
            try:
                data = self._get_json(
                    QUOTE_URL, {"symbols": ",".join(batch), "crumb": crumb, "fields": QUOTE_FIELDS}, timeout_s=30
                )
                for q in (data.get("quoteResponse") or {}).get("result") or []:
                    if q.get("symbol"):
                        out[q["symbol"]] = q
            except (YahooError, httpx.HTTPError, ValueError) as e:
                logger.warning("yahoo quotes batch %d failed: %s", i // BATCH, e)
            if i + BATCH < len(symbols):
                self.sleep(0.5)
        return out
