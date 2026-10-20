"""NAVER API HUB 뉴스 검색 어댑터 (`https://naverapihub.apigw.ntruss.com/search/v1/news`).

키 = `.env`(또는 SHARED_ENV_FILE) NAVER_CLIENT_ID / NAVER_CLIENT_SECRET → 헤더 X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY
(개발자센터 openapi.naver.com 아님 — 자산운용사 동향 프로젝트와 같은 키·엔드포인트). 값은 출력하지 않는다.
키가 없으면 `enabled=False` → collect_all이 이 소스를 건너뛴다(실패로 세지 않음).
검색어 1개 = 소스 1개, 최신순(sort=date). defaults.window(파이프라인이 넣음)가 있으면 윈도 안 기사가
나올 때까지 최대 naver_max_pages쪽을 넘긴다. 결과에 매체명이 없어 링크 도메인으로 publisher를 붙인다.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlsplit

from brief.config import PER_SOURCE_MAX
from brief.env import get_keys

from .base import Article, SourceResult
from .http import fetch_text

API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
KEY_NAMES = ["NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"]
MIN_INTERVAL_S = 1.0  # 요청 간격 기본값(어댑터 전체 공유). sources.yaml defaults.naver_min_interval_s로 조정

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_throttle_lock = threading.Lock()
_last_call = 0.0


def _throttle(min_interval_s: float) -> None:
    global _last_call
    with _throttle_lock:
        wait = _last_call + min_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _plain(text: str | None) -> str:
    # <b> 강조 태그는 공백 없이 제거(한국어 조사 분리 방지)
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub("", text or ""))).strip()


def _parse_pub(raw: str | None) -> datetime:
    try:
        dt = parsedate_to_datetime(raw or "")
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _publisher(url: str, domains: dict[str, str]) -> str | None:
    host = urlsplit(url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    for dom, name in domains.items():
        if host == dom or host.endswith("." + dom):
            return name
    return host or None


class NaverNewsAdapter:
    """검색어 하나를 수집하는 네이버 뉴스 어댑터."""

    default_timeout_s: float = 15.0
    default_retries: int = 2

    def __init__(self, source_cfg: dict, defaults: dict):
        self.cfg = source_cfg
        self.defaults = defaults
        self.keys = get_keys(KEY_NAMES)
        self.enabled = self.keys is not None

    def _build_article(self, item: dict) -> Article | None:
        title = _plain(item.get("title"))
        url = item.get("originallink") or item.get("link") or ""
        if not title or not url:
            return None
        return Article(
            title=title,
            url=url,
            published_at=_parse_pub(item.get("pubDate")),
            source=self.cfg["name"],
            category=self.cfg["category"],
            lang=self.cfg.get("lang", "ko"),
            summary=_plain(item.get("description"))[:400],
            publisher=_publisher(url, self.defaults.get("naver_media_domains") or {}),
        )

    def fetch(self) -> SourceResult:
        name = self.cfg["name"]
        if self.keys is None:
            raise RuntimeError(f".env에 {'/'.join(KEY_NAMES)} 없음")
        t0 = time.perf_counter()
        max_items = int(self.cfg.get("max_items", PER_SOURCE_MAX))
        # 파이프라인이 넘긴 수집 윈도(start, end). API에 날짜 필터가 없어 최신순으로 넘기며 윈도 안 기사를 모은다.
        window = self.defaults.get("window")
        pages = int(self.defaults.get("naver_max_pages", 4)) if window else 1
        display = 100 if window else min(100, max(max_items, 10))
        articles: list[Article] = []
        for page in range(pages):
            items = self._get_page(display, 1 + page * display)
            built = [a for a in (self._build_article(it) for it in items) if a is not None]
            if window is None:
                articles = built[:max_items]
                break
            articles += [a for a in built if window[0] <= a.published_at < window[1]]
            oldest = min((a.published_at for a in built), default=None)
            if len(articles) >= max_items or len(items) < display or (oldest is not None and oldest < window[0]):
                break
        articles = articles[:max_items]
        return SourceResult(name=name, ok=True, count=len(articles), articles=articles,
                            elapsed_s=time.perf_counter() - t0)

    def _get_page(self, display: int, start: int) -> list[dict]:
        params = {"query": self.cfg["query"], "display": display, "start": start, "sort": "date"}
        _throttle(float(self.defaults.get("naver_min_interval_s", MIN_INTERVAL_S)))
        text = fetch_text(
            f"{API_URL}?{urlencode(params)}",
            timeout_s=self.cfg.get("timeout_s", self.defaults.get("timeout_s", self.default_timeout_s)),
            retries=self.cfg.get("retries", self.defaults.get("retries", self.default_retries)),
            user_agent=self.defaults.get("user_agent", "MarketMorningBrief/0.1"),
            extra_headers={"X-NCP-APIGW-API-KEY-ID": self.keys[0], "X-NCP-APIGW-API-KEY": self.keys[1]},
        )
        return json.loads(text).get("items") or []
