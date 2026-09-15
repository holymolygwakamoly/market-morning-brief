"""일반 RSS 피드 어댑터 — feedparser로 파싱, 날짜/요약 정규화."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

from brief.config import PER_SOURCE_MAX

from .base import Article, SourceResult
from .http import fetch_text

logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def _clean_summary(raw: str) -> str:
    """HTML 태그를 제거하고 공백을 정리해 400자로 자른다."""
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ")
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:400]


def _parse_pub_date(entry: dict, tzname: str) -> datetime:
    """발행일을 tz-aware UTC datetime으로 변환한다.

    원본 문자열에 tz 정보가 없으면 소스의 `tz`로 보정한다.
    tz 정보가 있으면 feedparser가 UTC로 정규화한 `*_parsed`를 우선 사용한다.
    파싱 불가 시 now(UTC)를 사용한다(항목은 유지).
    """
    raw = entry.get("published") or entry.get("updated")
    if raw:
        try:
            dt = dateutil_parser.parse(raw)
        except (ValueError, OverflowError, TypeError):
            dt = None
        if dt is not None:
            if dt.tzinfo is None:
                try:
                    zone = ZoneInfo(tzname)
                except Exception:  # noqa: BLE001
                    zone = timezone.utc
                dt = dt.replace(tzinfo=zone)
                return dt.astimezone(timezone.utc)
            struct = entry.get("published_parsed") or entry.get("updated_parsed")
            if struct:
                try:
                    return datetime(*struct[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    pass
            return dt.astimezone(timezone.utc)

    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        try:
            return datetime(*struct[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc)


class RssAdapter:
    """일반 RSS 소스를 수집하는 기본 어댑터."""

    default_timeout_s: float = 15.0
    default_retries: int = 2

    def __init__(self, source_cfg: dict, defaults: dict):
        self.cfg = source_cfg
        self.defaults = defaults

    def _timeout_s(self) -> float:
        return self.cfg.get("timeout_s", self.defaults.get("timeout_s", self.default_timeout_s))

    def _retries(self) -> int:
        return self.cfg.get("retries", self.defaults.get("retries", self.default_retries))

    def _user_agent(self) -> str:
        return self.defaults.get("user_agent", "MarketMorningBrief/0.1")

    def _build_article(self, entry: dict) -> Article | None:
        title = entry.get("title")
        link = entry.get("link")
        if not title or not link:
            return None
        published_at = _parse_pub_date(entry, self.cfg.get("tz", "UTC"))
        summary_raw = entry.get("summary") or entry.get("description") or ""
        return Article(
            title=title.strip(),
            url=link,
            published_at=published_at,
            source=self.cfg["name"],
            category=self.cfg["category"],
            lang=self.cfg.get("lang", "en"),
            summary=_clean_summary(summary_raw),
        )

    def fetch(self) -> SourceResult:
        name = self.cfg["name"]
        t0 = time.perf_counter()
        text = fetch_text(
            self.cfg["url"],
            timeout_s=self._timeout_s(),
            retries=self._retries(),
            user_agent=self._user_agent(),
        )
        # feedparser에는 항상 텍스트만 전달한다 (URL을 넘기면 모킹이 우회됨).
        parsed = feedparser.parse(text)
        max_items = self.cfg.get("max_items", PER_SOURCE_MAX)
        articles: list[Article] = []
        for entry in parsed.entries[:max_items]:
            article = self._build_article(entry)
            if article is not None:
                articles.append(article)
        elapsed = time.perf_counter() - t0
        return SourceResult(name=name, ok=True, count=len(articles), articles=articles, elapsed_s=elapsed)
