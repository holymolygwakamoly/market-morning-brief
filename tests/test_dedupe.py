"""Step 2 수집 윈도·중복 제거 테스트."""
from __future__ import annotations

import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from brief.collect.base import Article
from brief.config import KST, MAX_ARTICLES, PER_SOURCE_MAX
from brief.dedupe import dedupe
from brief.window import filter_window, in_window, previous_business_day, window_start


def _article(
    title: str,
    url: str,
    source: str,
    *,
    lang: str = "ko",
    category: str = "KR",
    published_at: datetime | None = None,
    summary: str = "",
) -> Article:
    return Article(
        title=title,
        url=url,
        published_at=published_at or datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc),
        source=source,
        category=category,
        lang=lang,
        summary=summary,
    )


# --- dedupe: URL 정규화 + 제목 유사도 병합 ----------------------------------


def test_dedupe_merges_same_story_url_and_title():
    a1 = _article(
        "삼성전자, HBM 3세대 양산 돌입",
        "https://a.com/news/1?utm_source=fb",
        "SourceA",
        published_at=datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc),
    )
    a2 = _article(
        "삼성전자, HBM 3세대 양산 돌입",
        "https://a.com/news/1?utm_source=tw&utm_medium=x",
        "SourceB",
        published_at=datetime(2026, 9, 18, 9, 5, tzinfo=timezone.utc),
    )
    a3 = _article(
        "삼성전자 HBM3세대 양산돌입",
        "https://b.com/news/2",
        "SourceC",
        published_at=datetime(2026, 9, 18, 9, 10, tzinfo=timezone.utc),
    )
    result = dedupe([a1, a2, a3])
    assert len(result) == 1


def test_dedupe_keeps_different_language_same_story_separate():
    ko = _article(
        "삼성전자, HBM 3세대 양산 돌입",
        "https://a.com/1",
        "SourceA",
        lang="ko",
        category="KR",
        published_at=datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc),
    )
    en = _article(
        "Samsung starts HBM3 mass production",
        "https://b.com/2",
        "SourceB",
        lang="en",
        category="US",
        published_at=datetime(2026, 9, 18, 9, 5, tzinfo=timezone.utc),
    )
    result = dedupe([ko, en])
    assert len(result) == 2


# --- dedupe: 소스별 상한 + 전체 상한(다양성 유지) ---------------------------


def test_dedupe_per_source_cap_and_total_cap_with_diversity():
    n_sources = MAX_ARTICLES // PER_SOURCE_MAX  # 200 // 40 = 5
    rnd = random.Random(1234)

    def rand_hangul(n: int = 30) -> str:
        return "".join(chr(rnd.randint(0xAC00, 0xD7A3)) for _ in range(n))

    base = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
    articles: list[Article] = []
    for s in range(n_sources):
        src = f"Source{s}"
        for i in range(100):  # 소스당 상한(40)보다 많이 생성
            title = f"{src} idx{i:03d} {rand_hangul(30)}"
            articles.append(
                _article(
                    title,
                    f"https://{src.lower()}.example.com/{i}",
                    src,
                    lang="ko",
                    published_at=base + timedelta(minutes=i),
                )
            )

    result = dedupe(articles)

    assert len(result) == MAX_ARTICLES
    counts = Counter(a.source for a in result)
    for s in range(n_sources):
        assert counts[f"Source{s}"] == PER_SOURCE_MAX


# --- window: 직전 영업일 06:50 KST 이후 -------------------------------------


def test_previous_business_day_mapping():
    assert previous_business_day(date(2026, 9, 21)) == date(2026, 9, 18)  # Mon -> Fri
    assert previous_business_day(date(2026, 9, 22)) == date(2026, 9, 21)  # Tue -> Mon
    assert previous_business_day(date(2026, 9, 19)) == date(2026, 9, 18)  # Sat -> Fri
    assert previous_business_day(date(2026, 9, 20)) == date(2026, 9, 18)  # Sun -> Fri


def test_window_monday_includes_friday_evening_excludes_thursday():
    run_date = date(2026, 9, 21)  # Monday
    fri_evening = _article(
        "F", "https://x.com/f", "S", published_at=datetime(2026, 9, 18, 22, 0, tzinfo=KST)
    )
    thu_evening = _article(
        "T", "https://x.com/t", "S", published_at=datetime(2026, 9, 17, 20, 0, tzinfo=KST)
    )
    assert in_window(fri_evening, run_date)
    assert not in_window(thu_evening, run_date)
    assert filter_window([fri_evening, thu_evening], run_date) == [fri_evening]


def test_window_tuesday_excludes_sunday_includes_monday():
    run_date = date(2026, 9, 22)  # Tuesday
    sun_noon = _article(
        "S", "https://x.com/s", "S", published_at=datetime(2026, 9, 20, 12, 0, tzinfo=KST)
    )
    mon_noon = _article(
        "M", "https://x.com/m", "S", published_at=datetime(2026, 9, 21, 12, 0, tzinfo=KST)
    )
    assert not in_window(sun_noon, run_date)
    assert in_window(mon_noon, run_date)


def test_window_start_is_0650_kst_previous_business_day():
    ws = window_start(date(2026, 9, 21))
    assert ws == datetime(2026, 9, 18, 6, 50, tzinfo=KST)
