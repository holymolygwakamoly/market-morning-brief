"""시장 데이터 계층 테스트 — 세션 선택, 구성종목 파싱, 표 구성, Yahoo 배치 quote(모킹)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from brief.market import collect_market
from brief.market.base import Bar
from brief.market.constituents import build_table, load_members, parse_kind_members, parse_wiki_members
from brief.market.indices import build_index_quote
from brief.market.yahoo import CHART_URL, COOKIE_URL, CRUMB_URL, QUOTE_URL, YahooClient, parse_bars, pick_session

KST_DAY = 9 * 3600  # 로컬 자정 → UTC 변환 없이 정오 타임스탬프를 쓴다


def _ts(y: int, m: int, d: int, hour: int = 12) -> int:
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


def chart_result(symbol: str, days: list[tuple[int, int, int, float]], tz: str = "America/New_York", **meta) -> dict:
    return {
        "meta": {"symbol": symbol, "exchangeTimezoneName": tz, "currency": "USD", "regularMarketPrice": days[-1][3],
                 "regularMarketTime": _ts(*days[-1][:3], 20), **meta},
        "timestamp": [_ts(y, m, d, 14) for y, m, d, _ in days],
        "indicators": {"quote": [{"close": [c for *_, c in days], "volume": [100] * len(days)}]},
    }


# --- 세션 선택 -------------------------------------------------------------------


def test_parse_bars_and_pick_session_skips_intraday_bar():
    r = chart_result("^KS11", [(2026, 9, 15, 100.0), (2026, 9, 16, 102.0), (2026, 9, 17, 103.0)], tz="Asia/Seoul")
    bars, vols, tz = parse_bars(r)
    assert [b.date for b in bars] == [date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17)]
    assert tz == "Asia/Seoul"
    # 기준일 9/17 → 9/17 장중 봉 제외, 9/16 세션
    assert pick_session(bars, date(2026, 9, 17)) == 1
    assert pick_session(bars, date(2026, 9, 15)) is None


def test_build_index_quote_prev_session_change():
    r = chart_result("^GSPC", [(2026, 9, 14, 100.0), (2026, 9, 15, 110.0), (2026, 9, 16, 99.0)])
    q = build_index_quote({"symbol": "^GSPC", "name": "S&P 500", "region": "US", "group": "미국", "constituents": "us_sp500"}, r, date(2026, 9, 17))
    assert q.session_date == date(2026, 9, 16)
    assert q.change_pct == -10.0 and q.prev_close == 110.0 and q.change_abs == -11.0
    assert q.constituents_key == "us_sp500" and len(q.history) == 3
    # 기준일이 9/16이면 9/15 세션 (마지막 봉이 아니므로 asof는 그 날짜 자정)
    q2 = build_index_quote({"symbol": "^GSPC", "name": "x", "region": "US"}, r, date(2026, 9, 16))
    assert q2.session_date == date(2026, 9, 15) and q2.change_pct == 10.0
    assert q2.asof.date() == date(2026, 9, 15)


def test_build_index_quote_null_close_bars_are_dropped():
    r = chart_result("^DJI", [(2026, 9, 15, 100.0), (2026, 9, 16, 101.0)])
    r["indicators"]["quote"][0]["close"][1] = None
    q = build_index_quote({"symbol": "^DJI", "name": "다우", "region": "US"}, r, date(2026, 9, 17))
    assert q.session_date == date(2026, 9, 15)


# --- 구성종목 파싱 ---------------------------------------------------------------

WIKI_HTML = """
<table class="wikitable"><tr><th>Year</th><th>Close</th></tr>""" + "".join(f"<tr><td>{y}</td><td>1</td></tr>" for y in range(30)) + """</table>
<table id="constituents"><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>Sub</th></tr>
""" + "".join(f"<tr><td>T{i}</td><td>Co {i}</td><td>Tech</td><td>x</td></tr>" for i in range(25)) + """
<tr><td>BRK.B</td><td>Berkshire</td><td>Financials</td><td>x</td></tr></table>"""


def test_parse_wiki_members_picks_table_by_headers_and_normalizes_ticker():
    rows = parse_wiki_members(WIKI_HTML, ("Symbol", "Security", "GICS Sector"))
    assert len(rows) == 26
    assert rows[-1] == {"ticker": "BRK-B", "name": "Berkshire", "sector": "Financials"}
    with pytest.raises(ValueError):
        parse_wiki_members(WIKI_HTML, ("Ticker", "Company", "ICB Industry"))


def test_parse_kind_members_filters_alnum_codes():
    rows_html = "".join(
        f"<tr><td>회사{i}</td><td>유가</td><td>{code}</td><td>업종{i}</td><td>p</td></tr>"
        for i, code in enumerate([f"{n:06d}" for n in range(60)] + ["0220W0"])
    )
    html = "<table><tr><td>회사명</td><td>시장구분</td><td>종목코드</td><td>업종</td><td>주요제품</td></tr>" + rows_html + "</table>"
    rows = parse_kind_members(html.encode("euc-kr"), ".KS")
    assert len(rows) == 60 and rows[0]["ticker"] == "000000.KS" and rows[0]["sector"] == "업종0"


def test_load_members_uses_cache_then_stale_cache_on_failure(tmp_path: Path):
    cache = tmp_path / "c"
    cache.mkdir()
    (cache / "members_us_sp500.json").write_text(
        json.dumps({"fetched": "2020-01-01T00:00:00+00:00", "rows": [{"ticker": "AAPL", "name": "Apple", "sector": "IT"}]}),
        encoding="utf-8",
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies").mock(return_value=httpx.Response(503))
        with httpx.Client() as c:
            rows, src = load_members("us_sp500", cache, client=c, now=datetime(2026, 9, 17, tzinfo=timezone.utc))
    assert rows[0]["ticker"] == "AAPL" and src.startswith("stale-cache")


# --- 표 구성 -------------------------------------------------------------------


def test_build_table_values_and_intraday_note():
    members = [{"ticker": "AAPL", "name": "Apple", "sector": "IT"}, {"ticker": "MSFT", "name": "Microsoft", "sector": "IT"}, {"ticker": "GONE", "name": "x", "sector": None}]
    quotes = {
        "AAPL": {"regularMarketPrice": 100.0, "regularMarketChangePercent": 1.234, "regularMarketVolume": 10, "marketCap": 1e12, "marketState": "CLOSED", "regularMarketTime": _ts(2026, 9, 16, 20)},
        "MSFT": {"regularMarketPrice": 200.0, "regularMarketChangePercent": -0.5, "regularMarketVolume": 5, "marketCap": 2e12, "marketState": "CLOSED", "regularMarketTime": _ts(2026, 9, 16, 20)},
    }
    t = build_table("us_sp500", members, quotes, source="Wikipedia fetched", run_date=date(2026, 9, 17))
    assert t.coverage == (2, 3) and t.note is None and t.session_date == date(2026, 9, 16)
    a = t.rows[0]
    assert a.change_pct == 1.23 and a.value == 1000.0 and a.value_estimated and a.market_cap == 1e12
    for q in quotes.values():
        q["marketState"] = "REGULAR"
    t2 = build_table("kr_kospi", members, quotes, source="KIND", run_date=date(2026, 9, 17))
    assert t2.note and "장중" in t2.note


# --- Yahoo 배치 quote --------------------------------------------------------------


def test_yahoo_quotes_batches_with_crumb_and_isolates_failed_batch():
    symbols = [f"S{i}" for i in range(600)]
    calls: list[str] = []

    def quote_handler(request: httpx.Request) -> httpx.Response:
        syms = request.url.params["symbols"].split(",")
        calls.append(request.url.params["crumb"])
        if syms[0] == "S250":
            return httpx.Response(500)
        return httpx.Response(200, json={"quoteResponse": {"result": [{"symbol": s, "regularMarketPrice": 1.0} for s in syms]}})

    with respx.mock(assert_all_called=False) as mock:
        mock.get(COOKIE_URL).mock(return_value=httpx.Response(404))
        mock.get(CRUMB_URL).mock(return_value=httpx.Response(200, text="abc123"))
        mock.get(QUOTE_URL).mock(side_effect=quote_handler)
        y = YahooClient(httpx.Client(), retries=0, sleep=lambda s: None)
        out = y.quotes(symbols)
    assert set(calls) == {"abc123"} and len(calls) == 3
    assert len(out) == 350 and "S0" in out and "S250" not in out and "S599" in out


def test_collect_market_isolates_index_errors(tmp_path: Path):
    cfg = {
        "indices": [
            {"symbol": "^GSPC", "name": "S&P 500", "region": "US", "group": "미국"},
            {"symbol": "^BAD", "name": "bad", "region": "US", "group": "미국"},
        ],
        "constituents": [],
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get(CHART_URL.format(symbol="^GSPC")).mock(
            return_value=httpx.Response(200, json={"chart": {"result": [chart_result("^GSPC", [(2026, 9, 15, 1.0), (2026, 9, 16, 2.0)])], "error": None}})
        )
        mock.get(CHART_URL.format(symbol="^BAD")).mock(return_value=httpx.Response(404, json={"chart": {"result": None, "error": {"description": "No data"}}}))
        y = YahooClient(httpx.Client(), retries=0, sleep=lambda s: None)
        snap = collect_market(cfg, date(2026, 9, 17), cache_dir=tmp_path, yahoo=y)
    assert [q.symbol for q in snap.indices] == ["^GSPC"] and snap.indices[0].change_pct == 100.0
    assert len(snap.errors) == 1 and "^BAD" in snap.errors[0]
