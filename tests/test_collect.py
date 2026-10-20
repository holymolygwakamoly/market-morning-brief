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
    assert "market" in cfg and cfg["market"]["indices"] and set(cfg["market"]["constituents"]) == {"us_sp500", "us_ndx", "us_djia", "kr_kospi", "kr_kosdaq"}


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


# --- (g) 네이버 뉴스(API HUB): 헤더 키·파싱·키 없으면 생략 -------------------

NAVER_JSON = {"items": [
    {"title": "<b>코스피</b>, 외국인 매수에 &quot;반등&quot;", "originallink": "https://www.hankyung.com/article/1",
     "link": "https://n.news.naver.com/mnews/article/015/1", "description": "<b>코스피</b>가 올랐다",
     "pubDate": "Tue, 20 Oct 2026 09:30:00 +0900"},
    {"title": "", "originallink": "https://x.com/2", "link": "", "description": "", "pubDate": ""},
]}
NAVER_CFG = {"name": "네이버 코스피", "type": "naver_news", "query": "코스피", "category": "KR", "lang": "ko", "max_items": 25}


def _naver_keys(monkeypatch, present: bool):
    monkeypatch.setattr("brief.collect.naver.get_keys", lambda names: ["test-id", "test-secret"] if present else None)
    monkeypatch.setattr("brief.collect.naver._throttle", lambda s: None)


@respx.mock
def test_naver_news_sends_hub_headers_and_parses(monkeypatch):
    from brief.collect.naver import NaverNewsAdapter

    _naver_keys(monkeypatch, True)
    route = respx.get(url__regex=r"https://naverapihub\.apigw\.ntruss\.com/search/v1/news.*").mock(
        return_value=httpx.Response(200, json=NAVER_JSON)
    )
    r = NaverNewsAdapter(NAVER_CFG, {"naver_media_domains": {"hankyung.com": "한국경제"}}).fetch()

    req = route.calls[0].request
    assert req.headers["X-NCP-APIGW-API-KEY-ID"] == "test-id"
    assert req.headers["X-NCP-APIGW-API-KEY"] == "test-secret"
    assert req.url.params["query"] == "코스피" and req.url.params["sort"] == "date"
    assert r.ok and r.count == 1
    a = r.articles[0]
    assert a.title == '코스피, 외국인 매수에 "반등"'
    assert a.url == "https://www.hankyung.com/article/1"
    assert a.publisher == "한국경제" and a.summary == "코스피가 올랐다"
    assert a.published_at == datetime(2026, 10, 20, 0, 30, tzinfo=timezone.utc)


def test_naver_without_keys_is_skipped_not_failed(monkeypatch):
    _naver_keys(monkeypatch, False)
    cfg = {"defaults": {}, "sources": [NAVER_CFG]}
    assert collect_all(cfg) == []


def test_env_shared_file(tmp_path, monkeypatch):
    from brief.env import get_keys

    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SHARED_ENV_FILE", raising=False)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / ".env").write_text("NAVER_CLIENT_ID=a\nNAVER_CLIENT_SECRET=b\n", encoding="utf-8")
    root = tmp_path / "proj"
    root.mkdir()
    assert get_keys(["NAVER_CLIENT_ID"], root) is None
    (root / ".env").write_text("# c\nSHARED_ENV_FILE=../other/.env\nNAVER_CLIENT_SECRET=local\n", encoding="utf-8")
    assert get_keys(["NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"], root) == ["a", "local"]
    monkeypatch.setenv("NAVER_CLIENT_ID", "env")
    assert get_keys(["NAVER_CLIENT_ID"], root) == ["env"]


def test_naver_pages_until_window(monkeypatch):
    from brief.collect import naver

    _naver_keys(monkeypatch, True)
    # 1쪽 = 윈도 뒤(오후) 기사 100건, 2쪽 = 윈도 안 3건 + 윈도 앞 1건(여기서 멈춤)
    late = [{"title": f"late{i}", "link": f"https://a.kr/l{i}", "pubDate": "Tue, 20 Oct 2026 15:00:00 +0900"} for i in range(100)]
    inside = [{"title": f"in{i}", "link": f"https://a.kr/i{i}", "pubDate": "Tue, 20 Oct 2026 08:00:00 +0900"} for i in range(3)]
    old = [{"title": "old", "link": "https://a.kr/o", "pubDate": "Sun, 18 Oct 2026 08:00:00 +0900"}]
    pages = {1: late, 101: inside + old + late[:96]}
    calls: list[int] = []

    def fake_page(self, display, start):
        calls.append(start)
        return pages[start]

    monkeypatch.setattr(naver.NaverNewsAdapter, "_get_page", fake_page)
    from brief.window import window_end, window_start
    from datetime import date
    d = date(2026, 10, 20)
    r = naver.NaverNewsAdapter(NAVER_CFG, {"window": (window_start(d), window_end(d)), "naver_max_pages": 4}).fetch()
    assert calls == [1, 101]
    assert [a.title for a in r.articles] == ["in0", "in1", "in2"]
