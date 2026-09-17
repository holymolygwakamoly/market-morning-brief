"""분석 계층 스키마 — 전달용(output_format) 모델과 앱 측 검증 규칙.

전달용 모델(Stage1Result, TopicReportOut, SectorReportOut)은 구조화 출력 제약 때문에
길이·개수 제약(min_length/max_length/min_items/max_items)을 두지 않고 `extra="forbid"`만 지정한다.
길이·개수 검증은 `topic_report_violations` / `sector_report_violations`에서 수행한다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

SECTOR_CODES: dict[str, str] = {
    "SEMI": "반도체",
    "AI_SW": "AI 소프트웨어",
    "AI_INFRA": "AI 인프라/데이터센터",
    "BIG_TECH": "빅테크",
    "AUTO": "자동차",
    "EV_BATTERY": "2차전지",
    "BIO": "바이오/제약",
    "FIN": "금융/은행",
    "INSUR": "보험",
    "REALESTATE": "부동산/건설",
    "ENERGY": "에너지/정유",
    "UTIL": "유틸리티",
    "DEFENSE": "방산",
    "SHIP": "조선",
    "STEEL": "철강/소재",
    "CHEM": "화학",
    "RETAIL": "유통/소비재",
    "FOOD": "식음료",
    "ENT": "엔터/게임",
    "TELECOM": "통신",
    "AIRLINE": "항공/여행",
    "MACRO_RATES": "금리/채권",
    "FX": "환율",
    "COMMOD": "원자재",
    "CRYPTO": "가상자산",
    "ROBOT": "로봇",
    "SPACE": "우주항공",
    "MEDIA": "미디어/플랫폼",
    "HEALTHCARE": "헬스케어 기기",
    "GEOPOL": "지정학/전쟁/관세",
    "OTHER": "기타",
}


# --- 전달용 (output_format) -----------------------------------------------------


class Stage1Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    i: int
    p: int
    m: Literal["US", "EU", "KR", "CN", "JP", "MACRO", "GLOBAL"]
    a: bool
    s: list[str]
    k: str


class Stage1Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Stage1Item]


class Stage1Tags(BaseModel):
    """stage1 배치 병합 결과(앱 내부용, CLI 스키마 아님). partial=일부 배치 실패."""

    items: list[Stage1Item]
    partial: bool = False


class LeaderOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ticker: str | None = None
    comment: str


AI_SECTOR_MIN_CHARS = 300


# --- v4 토픽 보고서 (PLAN §11.4) --------------------------------------------------


class HeadlineOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    importance: int  # 1~5


class SectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str
    body: str
    bullets: list[str]


class TopicReportOut(BaseModel):
    """거시경제·미국·유럽·한국·중국 보고서 공통 스키마(전달용)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    overview: str
    sections: list[SectionOut]
    leaders: list[LeaderOut]
    events_today: list[str]
    headlines: list[HeadlineOut]
    data_caveats: list[str]
    disclaimer: str


class SectorItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    direction: Literal["issue", "up", "down"]
    markets: list[str]  # US / EU / KR / CN 중 영향 시장
    reason: str
    news: str
    leaders: list[LeaderOut]
    etf_note: str | None = None


class SectorReportOut(BaseModel):
    """섹터별 주요뉴스 보고서(전달용)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    overview: str
    sectors: list[SectorItemOut]
    ai_sector: str
    cross_events: list[SectionOut]
    events_today: list[str]
    headlines: list[HeadlineOut]
    data_caveats: list[str]
    disclaimer: str


TOPIC_MIN_SECTIONS = 4
SECTION_MIN_CHARS = 200
OVERVIEW_MIN_CHARS = 150
SECTOR_MIN_ITEMS = 3
SECTOR_REASON_MIN_CHARS = 100
LENGTH_PREFIX = "[length]"


def _check_headlines(out: TopicReportOut | SectorReportOut, errs: list[str]) -> None:
    if not 1 <= len(out.headlines) <= 3 or any(not h.text.strip() for h in out.headlines):
        errs.append(f"headlines는 1~3개여야 합니다 (현재 {len(out.headlines)}개)")
    for h in out.headlines:
        if not 1 <= h.importance <= 5:
            errs.append(f"headlines.importance는 1~5 (현재 {h.importance})")


def topic_report_violations(out: TopicReportOut, disclaimer: str) -> list[str]:
    """분량 규칙은 `[length]` 접두어 — 마지막 시도에서 분량만 미달이면 완화 게시."""
    errs: list[str] = []
    if len(out.overview) < OVERVIEW_MIN_CHARS:
        errs.append(f"{LENGTH_PREFIX} overview는 {OVERVIEW_MIN_CHARS}자 이상 (현재 {len(out.overview)}자)")
    if len(out.sections) < TOPIC_MIN_SECTIONS:
        errs.append(f"{LENGTH_PREFIX} sections는 {TOPIC_MIN_SECTIONS}개 이상 (현재 {len(out.sections)}개)")
    for i, s in enumerate(out.sections):
        if not s.heading.strip():
            errs.append(f"sections[{i}] heading 비어 있음")
        if len(s.body) < SECTION_MIN_CHARS:
            errs.append(f"{LENGTH_PREFIX} sections[{i}] '{s.heading}' body는 {SECTION_MIN_CHARS}자 이상 (현재 {len(s.body)}자)")
    _check_headlines(out, errs)
    if out.disclaimer != disclaimer:
        errs.append("disclaimer 문구가 지정된 문장과 다릅니다")
    return errs


def sector_report_violations(out: SectorReportOut, disclaimer: str) -> list[str]:
    errs: list[str] = []
    if len(out.overview) < OVERVIEW_MIN_CHARS:
        errs.append(f"{LENGTH_PREFIX} overview는 {OVERVIEW_MIN_CHARS}자 이상 (현재 {len(out.overview)}자)")
    if len(out.sectors) < SECTOR_MIN_ITEMS:
        errs.append(f"{LENGTH_PREFIX} sectors는 {SECTOR_MIN_ITEMS}개 이상 (현재 {len(out.sectors)}개)")
    for i, s in enumerate(out.sectors):
        if len(s.reason) < SECTOR_REASON_MIN_CHARS:
            errs.append(f"{LENGTH_PREFIX} sectors[{i}] '{s.name}' reason은 {SECTOR_REASON_MIN_CHARS}자 이상 (현재 {len(s.reason)}자)")
        if not 1 <= len(s.leaders) <= 5:
            errs.append(f"sectors[{i}] '{s.name}' leaders는 1~5개 (현재 {len(s.leaders)}개)")
    if len(out.ai_sector) < AI_SECTOR_MIN_CHARS:
        errs.append(f"{LENGTH_PREFIX} ai_sector는 {AI_SECTOR_MIN_CHARS}자 이상 (현재 {len(out.ai_sector)}자)")
    _check_headlines(out, errs)
    if out.disclaimer != disclaimer:
        errs.append("disclaimer 문구가 지정된 문장과 다릅니다")
    return errs
