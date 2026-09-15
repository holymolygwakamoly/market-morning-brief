"""수집 윈도 — 직전 영업일 06:50 KST 이후 기사만 포함."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from brief.collect.base import Article
from brief.config import KST

# 요일별 직전 영업일까지의 일수. Mon(0)->Fri(-3), Sat(5)->Fri(-1), Sun(6)->Fri(-2), 그 외 -1일.
_DAYS_BACK = {0: 3, 5: 1, 6: 2}


def previous_business_day(d: date) -> date:
    """d의 직전 영업일(월~금)을 반환한다."""
    return d - timedelta(days=_DAYS_BACK.get(d.weekday(), 1))


def window_start(run_date: date) -> datetime:
    """직전 영업일 06:50 KST를 tz-aware datetime으로 반환한다."""
    pbd = previous_business_day(run_date)
    return datetime(pbd.year, pbd.month, pbd.day, 6, 50, tzinfo=KST)


def in_window(article: Article, run_date: date, end: datetime | None = None) -> bool:
    """start <= published_at < end 인지 판정한다.

    end 기본값은 늦게 들어온 기사를 포함하기 위한 여유 3시간을 더한
    run_date 09:50 KST.
    """
    start = window_start(run_date)
    if end is None:
        end = datetime(run_date.year, run_date.month, run_date.day, 9, 50, tzinfo=KST)
    return start <= article.published_at < end


def filter_window(articles: list[Article], run_date: date) -> list[Article]:
    """수집 윈도에 속한 기사만 남긴다."""
    return [a for a in articles if in_window(a, run_date)]
