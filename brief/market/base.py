"""시장 데이터 계층 공용 모델 (v4 리서치 대시보드, PLAN §11.3).

모든 시각은 tz-aware UTC, `session_date`는 거래소 현지 날짜.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, field_validator

Region = Literal["US", "EU", "KR", "CN", "JP", "MACRO"]
Kind = Literal["index", "fx", "rate", "commodity", "crypto", "etf"]


class Bar(BaseModel):
    date: date
    close: float


class IndexQuote(BaseModel):
    """지수·환율·금리·원자재·ETF 한 심볼의 직전 완료 세션 시세."""

    symbol: str
    name: str
    region: Region
    kind: Kind = "index"
    close: float
    prev_close: float | None = None
    change_pct: float
    change_abs: float | None = None
    session_date: date
    asof: datetime
    currency: str | None = None
    history: list[Bar] = []
    constituents_key: str | None = None  # 구성종목 표가 있는 지수(us_sp500 등)
    group: str | None = None  # 홈 카드 그룹(예: "미국", "거시")

    @field_validator("asof")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("asof must be timezone-aware")
        return v.astimezone(timezone.utc)


class Constituent(BaseModel):
    ticker: str
    name: str
    sector: str | None = None
    close: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    value: float | None = None  # 거래대금 (소스가 주면 실제, 없으면 volume×close 추정)
    value_estimated: bool = False
    market_cap: float | None = None


class ConstituentTable(BaseModel):
    key: str  # us_sp500 / us_ndx / us_djia / kr_kospi / kr_kosdaq
    index_name: str
    session_date: date | None = None
    currency: str
    rows: list[Constituent]
    total: int  # 지수 구성종목 수(알려진 경우) — coverage = len(rows)/total
    source: str
    note: str | None = None
    error: str | None = None

    @property
    def coverage(self) -> tuple[int, int]:
        return len(self.rows), self.total


class InvestorFlow(BaseModel):
    """KRX 투자자별 순매수 (억원 단위로 저장, 양수=순매수)."""

    market: str  # KOSPI / KOSDAQ
    session_date: date | None = None
    foreign: float | None = None
    institution: float | None = None
    individual: float | None = None
    detail: dict[str, float] = {}
    source: str = "KRX"
    error: str | None = None


class MarketSnapshot(BaseModel):
    run_date: date
    indices: list[IndexQuote] = []
    constituents: dict[str, ConstituentTable] = {}
    flows: list[InvestorFlow] = []
    errors: list[str] = []
    elapsed_s: float = 0.0

    def by_symbol(self) -> dict[str, IndexQuote]:
        return {q.symbol: q for q in self.indices}

    def by_region(self, region: str) -> list[IndexQuote]:
        return [q for q in self.indices if q.region == region]
