"""URL/제목 정규화 + 문자 bigram 유사도 기반 중복 제거."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from brief.collect.base import Article
from brief.config import MAX_ARTICLES, PER_SOURCE_MAX

_DROP_PREFIXES = ("utm_",)
_DROP_EXACT = {"fbclid", "gclid"}

_TITLE_SUFFIX_RE = re.compile(r"\s*-\s*[^-]+$")
_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


def normalize_url(url: str) -> str:
    """호스트 소문자화, utm_*/fbclid/gclid 제거, fragment 제거, 끝 슬래시 제거."""
    parts = urlsplit(url)
    netloc = parts.netloc.lower()
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_DROP_PREFIXES) and k.lower() not in _DROP_EXACT
    ]
    query = urlencode(query_pairs)
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def normalize_title(t: str) -> str:
    """` - 출처` 형태의 접미사 제거, 소문자화, 구두점/공백 제거."""
    t = _TITLE_SUFFIX_RE.sub("", t)
    t = t.lower()
    return _NON_WORD_RE.sub("", t)


def _char_bigrams(s: str) -> list[str]:
    if len(s) < 2:
        return [s] if s else []
    return [s[i : i + 2] for i in range(len(s) - 1)]


def bigram_dice(a: str, b: str) -> float:
    """문자 bigram 기반 Dice 유사도 (0~1)."""
    ba, bb = _char_bigrams(a), _char_bigrams(b)
    if not ba or not bb:
        return 1.0 if a == b else 0.0
    ca, cb = Counter(ba), Counter(bb)
    overlap = sum((ca & cb).values())
    return 2 * overlap / (len(ba) + len(bb))


class _DSU:
    """간단한 Union-Find (클러스터 병합용)."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[rj] = ri


def dedupe(
    articles: list[Article],
    *,
    threshold: float = 0.6,
    per_source_max: int = PER_SOURCE_MAX,
    max_total: int = MAX_ARTICLES,
) -> list[Article]:
    """URL 정확 일치 + 제목 유사도(같은 lang) 병합 후 소스 다양성을 유지하며 상한을 적용한다."""
    n = len(articles)
    if n == 0:
        return []

    dsu = _DSU(n)
    norm_urls = [normalize_url(a.url) for a in articles]
    norm_titles = [normalize_title(a.title) for a in articles]

    # 1) URL 정확 일치 병합
    url_to_idx: dict[str, int] = {}
    for i, u in enumerate(norm_urls):
        if u in url_to_idx:
            dsu.union(url_to_idx[u], i)
        else:
            url_to_idx[u] = i

    # 2) 제목 유사도 병합 (같은 lang에서만)
    for i in range(n):
        for j in range(i + 1, n):
            if articles[i].lang != articles[j].lang:
                continue
            if dsu.find(i) == dsu.find(j):
                continue
            if bigram_dice(norm_titles[i], norm_titles[j]) >= threshold:
                dsu.union(i, j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[dsu.find(i)].append(i)

    merged: list[Article] = []
    for idxs in clusters.values():
        best = min(idxs, key=lambda i: (articles[i].published_at, -len(articles[i].summary)))
        merged.append(articles[best])

    # 3) 소스별 상한 (최신순)
    by_source: dict[str, list[Article]] = defaultdict(list)
    for a in merged:
        by_source[a.source].append(a)
    for src, arts in by_source.items():
        arts.sort(key=lambda a: a.published_at, reverse=True)
        by_source[src] = arts[:per_source_max]

    # 4) 전체 상한 — 소스 라운드로빈(최신순)으로 다양성 유지
    queues = {src: list(arts) for src, arts in by_source.items()}
    src_names = list(queues.keys())
    result: list[Article] = []
    idx = 0
    while len(result) < max_total and any(queues[s] for s in src_names):
        src = src_names[idx % len(src_names)]
        if queues[src]:
            result.append(queues[src].pop(0))
        idx += 1
    return result
