"""지수 구성종목 표 — 미국(S&P500·나스닥100·다우30)·한국(코스피·코스닥) 전 종목 (결정 15).

구성종목 목록: 미국 = Wikipedia 구성종목 표(S&P500·나스닥100, CC-BY-SA) + stockanalysis.com(다우30), 한국 = KIND 상장법인 목록(회사명·종목코드·업종).
목록은 `cache_dir/members_{key}.json`에 7일 캐시하고 갱신 실패 시 오래된 캐시를 그대로 쓴다.
시세: Yahoo v7 배치 quote(시총·거래량·등락률). 거래대금은 `거래량×종가` 추정(`value_estimated=true`).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from brief.market.base import Constituent, ConstituentTable
from brief.market.yahoo import BROWSER_UA, YahooClient

logger = logging.getLogger(__name__)

WIKI_UA = "MarketMorningBrief/0.1 (+https://github.com/holymolygwakamoly/market-morning-brief; contact via repo issues)"
KIND_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do"
CACHE_TTL = timedelta(days=7)

# key → (지수명, 통화, 소스 종류, 소스 인자)
INDEX_SPECS: dict[str, dict[str, Any]] = {
    "us_sp500": {"name": "S&P 500", "currency": "USD", "kind": "wiki",
                 "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "cols": ("Symbol", "Security", "GICS Sector")},
    "us_ndx": {"name": "나스닥 100", "currency": "USD", "kind": "wiki",
               "url": "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies", "cols": ("Ticker", "Company", "ICB Industry")},
    # 다우 30: Wikipedia 본문에서 구성종목 표가 사라져(2026-09 확인) stockanalysis.com 목록(robots 허용) 사용. 섹터는 S&P500 목록에서 보충.
    "us_djia": {"name": "다우 30", "currency": "USD", "kind": "stockanalysis",
                "url": "https://stockanalysis.com/list/dow-jones-stocks/", "cols": ("Symbol", "Company Name", None)},
    "kr_kospi": {"name": "코스피", "currency": "KRW", "kind": "kind", "market": "stockMkt", "suffix": ".KS"},
    "kr_kosdaq": {"name": "코스닥", "currency": "KRW", "kind": "kind", "market": "kosdaqMkt", "suffix": ".KQ"},
}


# --- 구성종목 목록 ---------------------------------------------------------------


def _norm_header(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip().lower()


def parse_wiki_members(html: str, cols: tuple[str, str, str | None]) -> list[dict]:
    """헤더에 `cols`(티커, 이름, 섹터|None)를 가진 첫 표(20행 이상) → [{ticker, name, sector}]. 모든 <table>을 본다(클래스 무관)."""
    soup = BeautifulSoup(html, "html.parser")
    want = [_norm_header(c) for c in cols if c]
    for table in soup.find_all("table"):
        first = table.find("tr")
        if first is None:
            continue
        headers = [_norm_header(th.get_text(" ")) for th in first.find_all(["th", "td"])]
        pos: list[int] = []
        for w in want:
            hit = next((i for i, h in enumerate(headers) if h.startswith(w)), None)
            if hit is None:
                break
            pos.append(hit)
        if len(pos) != len(want):
            continue
        rows: list[dict] = []
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) <= max(pos):
                continue
            ticker = cells[pos[0]].strip()
            if not ticker or not re.match(r"^[A-Z][A-Z0-9.\-]{0,6}$", ticker):
                continue
            sector = cells[pos[2]] if len(pos) > 2 else None
            rows.append({"ticker": ticker.replace(".", "-"), "name": cells[pos[1]], "sector": sector or None})
        if len(rows) >= 20:
            return rows
    raise ValueError(f"구성종목 표를 찾지 못함 (cols={cols})")


def parse_kind_members(raw: bytes, suffix: str) -> list[dict]:
    """KIND 상장법인 목록(EUC-KR Excel-HTML) → [{ticker, name, sector}] (6자리 숫자 코드만)."""
    soup = BeautifulSoup(raw.decode("euc-kr", errors="replace"), "html.parser")
    trs = soup.find_all("tr")
    if not trs:
        raise ValueError("KIND 목록 표가 비어 있음")
    headers = [c.get_text(strip=True) for c in trs[0].find_all(["th", "td"])]
    try:
        i_name, i_code, i_sector = headers.index("회사명"), headers.index("종목코드"), headers.index("업종")
    except ValueError as e:
        raise ValueError(f"KIND 헤더 불일치: {headers}") from e
    rows: list[dict] = []
    for tr in trs[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) <= max(i_name, i_code, i_sector):
            continue
        code = cells[i_code]
        if not re.fullmatch(r"\d{6}", code):
            continue  # 영문 포함 신규 코드는 Yahoo에 없음
        rows.append({"ticker": code + suffix, "name": cells[i_name], "sector": cells[i_sector] or None})
    if len(rows) < 50:
        raise ValueError(f"KIND 목록이 너무 적음: {len(rows)}")
    return rows


def fetch_members(key: str, *, client: httpx.Client, timeout_s: float = 60.0) -> list[dict]:
    spec = INDEX_SPECS[key]
    if spec["kind"] in ("wiki", "stockanalysis"):
        ua = WIKI_UA if spec["kind"] == "wiki" else BROWSER_UA
        resp = client.get(spec["url"], headers={"User-Agent": ua}, timeout=timeout_s, follow_redirects=True)
        resp.raise_for_status()
        rows = parse_wiki_members(resp.text, spec["cols"])
        if spec["kind"] == "stockanalysis":  # 섹터 없음 → S&P500 목록으로 보충(다우 30은 전부 S&P500 소속)
            try:
                sp = {m["ticker"]: m.get("sector") for m in fetch_members("us_sp500", client=client, timeout_s=timeout_s)}
                for m in rows:
                    m["sector"] = sp.get(m["ticker"])
            except Exception as e:  # noqa: BLE001
                logger.warning("sector fill for %s failed: %s", key, e)
        return rows
    resp = client.get(
        KIND_URL, params={"method": "download", "searchType": "13", "marketType": spec["market"]},
        headers={"User-Agent": BROWSER_UA}, timeout=timeout_s, follow_redirects=True,
    )
    resp.raise_for_status()
    return parse_kind_members(resp.content, spec["suffix"])


def load_members(key: str, cache_dir: Path, *, client: httpx.Client, now: datetime | None = None) -> tuple[list[dict], str]:
    """(목록, 출처 설명). 캐시 7일 이내면 캐시, 아니면 갱신 시도 후 실패 시 오래된 캐시."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / f"members_{key}.json"
    now = now or datetime.now(timezone.utc)
    cached: dict | None = None
    if p.exists():
        try:
            cached = json.loads(p.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(cached["fetched"])
            if now - fetched < CACHE_TTL and cached.get("rows"):
                return cached["rows"], f"cache({fetched.date()})"
        except (ValueError, KeyError, OSError):
            cached = None
    try:
        rows = fetch_members(key, client=client)
        p.write_text(json.dumps({"fetched": now.isoformat(), "rows": rows}, ensure_ascii=False), encoding="utf-8")
        return rows, "fetched"
    except Exception as e:  # noqa: BLE001
        if cached and cached.get("rows"):
            logger.warning("members %s refresh failed (%s); using stale cache", key, e)
            return cached["rows"], f"stale-cache({cached.get('fetched', '')[:10]})"
        raise


# --- 표 구성 -------------------------------------------------------------------


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def build_table(key: str, members: list[dict], quotes: dict[str, dict], *, source: str, run_date: date) -> ConstituentTable:
    spec = INDEX_SPECS[key]
    rows: list[Constituent] = []
    states: dict[str, int] = {}
    latest_ts = 0
    for m in members:
        q = quotes.get(m["ticker"])
        if not q or q.get("regularMarketPrice") is None:
            continue
        close = _num(q.get("regularMarketPrice"))
        vol = _num(q.get("regularMarketVolume"))
        rows.append(
            Constituent(
                ticker=m["ticker"],
                name=m.get("name") or q.get("shortName") or m["ticker"],
                sector=m.get("sector"),
                close=close,
                change_pct=round(_num(q.get("regularMarketChangePercent")) or 0.0, 2),
                volume=vol,
                value=(vol * close) if (vol is not None and close is not None) else None,
                value_estimated=True,
                market_cap=_num(q.get("marketCap")),
            )
        )
        st = q.get("marketState") or "?"
        states[st] = states.get(st, 0) + 1
        latest_ts = max(latest_ts, int(q.get("regularMarketTime") or 0))
    session_date = datetime.fromtimestamp(latest_ts, tz=timezone.utc).date() if latest_ts else None
    intraday = states.get("REGULAR", 0) > len(rows) / 2 if rows else False
    note = None
    if intraday:
        note = "장중 값입니다(업데이트 시각 기준). 장 마감 후 업데이트하면 종가 기준으로 표시됩니다."
    return ConstituentTable(
        key=key, index_name=spec["name"], session_date=session_date, currency=spec["currency"],
        rows=rows, total=len(members), source=f"Yahoo v7 quote + {source}", note=note,
        error=None if rows else "시세를 하나도 받지 못함",
    )


def fetch_constituents(
    keys: list[str],
    run_date: date,
    *,
    yahoo: YahooClient,
    cache_dir: Path,
    http: httpx.Client | None = None,
    budget_s: float = 240.0,
) -> dict[str, ConstituentTable]:
    """키별 독립 예외 격리. 예산 초과 시 남은 지수는 error 표로."""
    t0 = time.monotonic()
    out: dict[str, ConstituentTable] = {}
    owns = http is None
    client = http or httpx.Client(follow_redirects=True)
    try:
        for key in keys:
            spec = INDEX_SPECS[key]
            remaining = budget_s - (time.monotonic() - t0)
            if remaining < 20:
                out[key] = ConstituentTable(key=key, index_name=spec["name"], currency=spec["currency"], rows=[], total=0,
                                            source="-", error="시간 예산 초과로 건너뜀")
                continue
            try:
                members, src = load_members(key, cache_dir, client=client)
                quotes = yahoo.quotes([m["ticker"] for m in members], budget_s=remaining)
                out[key] = build_table(key, members, quotes, source=f"{'Wikipedia' if spec['kind'] == 'wiki' else 'KIND'} {src}", run_date=run_date)
            except Exception as e:  # noqa: BLE001
                logger.warning("constituents %s failed: %s", key, e)
                out[key] = ConstituentTable(key=key, index_name=spec["name"], currency=spec["currency"], rows=[], total=0,
                                            source="-", error=f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        if owns:
            client.close()
    return out
