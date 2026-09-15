"""수집 계층 공용 모델 — Article/Quote/SourceResult."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, field_validator


class Article(BaseModel):
    title: str
    url: str
    published_at: datetime
    source: str
    category: Literal["US", "KR", "MACRO"]
    lang: str
    summary: str = ""
    publisher: str | None = None

    @field_validator("published_at")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        return v.astimezone(timezone.utc)


class Quote(BaseModel):
    symbol: str
    name: str
    close: float
    change_pct: float
    asof: datetime
    prev_close: float | None = None

    @field_validator("asof")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("asof must be timezone-aware")
        return v.astimezone(timezone.utc)


class SourceResult(BaseModel):
    name: str
    ok: bool
    count: int
    error: str | None = None
    articles: list[Article] = []
    elapsed_s: float = 0.0
