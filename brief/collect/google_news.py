"""Google News RSS 어댑터 — 제목 접미사 제거·publisher 추출."""
from __future__ import annotations

import re

from .base import Article
from .rss import RssAdapter

# 제목 끝의 " - <출처>" 접미사. [^-]+로 마지막 " - "만 매칭한다.
_TITLE_SUFFIX_RE = re.compile(r"\s+-\s+([^-]+)$")


class GoogleNewsAdapter(RssAdapter):
    """Google News 검색 RSS 어댑터. Google 리다이렉트 링크를 그대로 보관한다."""

    default_timeout_s = 20.0
    default_retries = 3

    def _build_article(self, entry: dict) -> Article | None:
        article = super()._build_article(entry)
        if article is None:
            return None

        publisher: str | None = None
        source_el = entry.get("source")
        if source_el is not None:
            publisher = getattr(source_el, "title", None)
            if publisher is None and isinstance(source_el, dict):
                publisher = source_el.get("title")

        title = article.title
        m = _TITLE_SUFFIX_RE.search(title)
        if m:
            title = title[: m.start()].rstrip()
            if publisher is None:
                publisher = m.group(1).strip()

        source_name = self.cfg.get("source_name")
        if source_name:
            publisher = source_name

        return article.model_copy(update={
            "title": title,
            "publisher": publisher,
            "source": self.cfg["name"],
        })
