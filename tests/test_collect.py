"""Step 1 수집 계층 테스트."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import feedparser
import httpx
import pytest
import respx

from brief.collect import _build_adapter, articles_from, collect_all
from brief.collect.base import Article, SourceResult
from brief.collect.google_news import GoogleNewsAdapter
from brief.collect.rss import RssAdapter, _parse_pub_date
from brief.collect.yahoo_chart import fetch_quotes
from brief.config import ADAPTER_TYPES, load_sources

THEBELL_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title>삼성전자 반도체 소식</title>
  <link>https://news.google.com/rss/articles/abc123?oc=5</link>
  <pubDate>Mon, 15 Sep 2026 01:00:00 GMT</pubDate>
  <source url="https://www.thebell.co.kr">더벨</source>
</item>
</channel></rss>"""


# --- (a) collect_all은 절반 실패에도 전체 결과를 반환한다 -------------------


class _FakeAdapter:
    def __init__(self, name: str, should_fail: bool):
        self.cfg = {"name": name}
        self.should_fail = should_fail

    def fetch(self) -> SourceResult:
        if self.should_fail:
            raise TimeoutError(f"timeout: {self.cfg['name']}")
        article = Article(
            title="title",
            url=f"https://example.com/{self.cfg['name']}",
            published_at=datetime.now(timezone.utc),
            source=self.cfg["name"],
            category="US",
            lang="en",
            summary="s",
        )
        return SourceResult(name=self.cfg["name"], ok=True, count=1, articles=[article], elapsed_s=0.01)


def test_collect_all_isolates_failures():
    adapters = [_FakeAdapter(f"src{i}", should_fail=(i % 2 == 0)) for i in range(6)]
    results = collect_all({}, adapters=adapters)
    assert len(results) == 6
    ok_results = [r for r in results if r.ok]
    fail_results = [r for r in results if not r.ok]
    assert len(ok_results) == 3
    assert len(fail_results) == 3
    for r in ok_results:
        assert len(r.articles) == 1
    for r in fail_results:
        assert r.error is not None
    assert len(articles_from(results)) == 3


# --- (b) 더벨(Google News) 어댑터는 news.google.com만 호출한다 --------------


@respx.mock
def test_google_news_never_calls_thebell_host():
    cfg = load_sources()
    thebell_cfg = next(
        s for s in cfg["sources"] if s["type"] == "google_news" and s.get("source_name") == "더벨"
    )
    respx.get(url__regex=r"https://news\.google\.com/.*").mock(
        return_value=httpx.Response(200, text=THEBELL_RSS)
    )

    adapter = GoogleNewsAdapter(thebell_cfg, cfg["defaults"])
    result = adapter.fetch()

    hosts = {call.request.url.host for call in respx.calls}
    assert "news.google.com" in hosts
    assert not any("thebell.co.kr" in h for h in hosts)
    assert result.ok
    assert result.count == 1
    assert result.articles[0].publisher == "더벨"


# --- (c) 어댑터 타입 화이트리스트 -------------------------------------------


def test_sources_yaml_types_are_whitelisted():
    cfg = load_sources()
    for s in cfg["sources"]:
        assert s["type"] in ADAPTER_TYPES
    assert cfg["quotes"]["type"] in ADAPTER_TYPES


def test_unknown_adapter_type_raises_value_error():
    with pytest.raises(ValueError):
        _build_adapter({"type": "html_crawl", "name": "bad"}, {})


# --- (d) naive pubDate는 소스 tz로 보정된다 ---------------------------------


def test_naive_pubdate_localized_with_source_tz():
    entry = {"published": "Mon, 15 Sep 2026 08:00:00"}
    dt = _parse_pub_date(entry, "Asia/Seoul")
    assert dt == datetime(2026, 9, 14, 23, 0, 0, tzinfo=timezone.utc)


# --- (e) Google News 제목 접미사 제거 + publisher 추출 ----------------------


def test_google_news_title_suffix_and_publisher():
    cfg = {"name": "Test GN", "category": "KR", "lang": "ko", "tz": "UTC"}
    defaults = {"user_agent": "UA", "timeout_s": 20, "retries": 3}
    entry = {
        "title": "삼성전자, HBM 증설 - 더벨",
        "link": "https://news.google.com/rss/articles/xyz",
        "published": "Mon, 15 Sep 2026 01:00:00 GMT",
    }
    adapter = GoogleNewsAdapter(cfg, defaults)
    article = adapter._build_article(entry)
    assert article is not None
    assert article.title == "삼성전자, HBM 증설"
    assert article.publisher == "더벨"


# --- (f) feedparser.parse에는 텍스트만 전달된다 (URL 금지) ------------------


def test_feedparser_never_receives_url(monkeypatch):
    captured: dict = {}

    def fake_parse(arg):
        captured["arg"] = arg
        return SimpleNamespace(entries=[])

    monkeypatch.setattr(feedparser, "parse", fake_parse)
    monkeypatch.setattr("brief.collect.rss.fetch_text", lambda *a, **k: "<rss></rss>")

    cfg = {"name": "X", "url": "http://example.com/rss", "category": "US", "lang": "en", "tz": "UTC"}
    defaults = {"user_agent": "UA", "timeout_s": 15, "retries": 2}
    adapter = RssAdapter(cfg, defaults)
    adapter.fetch()

    assert "arg" in captured
    assert not captured["arg"].startswith("http")


# --- (g) Yahoo chart JSON 파싱 + change_pct 계산 ----------------------------


def test_yahoo_chart_quote_change_pct(monkeypatch):
    fixture = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": 105.0,
                        "chartPreviousClose": 100.0,
                        "regularMarketTime": 1758000000,
                    }
                }
            ]
        }
    }
    monkeypatch.setattr(
        "brief.collect.yahoo_chart.fetch_text", lambda *a, **k: json.dumps(fixture)
    )
    quotes_cfg = {
        "url_template": "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        "timeout_s": 15,
        "retries": 2,
        "symbols": [{"symbol": "^GSPC", "name": "S&P 500"}],
    }
    defaults = {"user_agent": "UA"}
    quotes, errors = fetch_quotes(quotes_cfg, defaults)

    assert errors == []
    assert len(quotes) == 1
    q = quotes[0]
    assert q.symbol == "^GSPC"
    assert q.close == 105.0
    assert q.prev_close == 100.0
    assert q.change_pct == 5.0
