"""분석 계층 스키마 — 전달용(output_format) 모델과 앱 측 검증용 Report.

전달용 모델(Stage1Result, ReportOut)은 구조화 출력 API 제약 때문에
길이·개수 제약(min_length/max_length/min_items/max_items)을 두지 않고
`extra="forbid"`만 지정한다. 길이·개수 검증은 `Report`에서 수행한다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

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
    "OTHER": "기타",
}


# --- 전달용 (output_format) -----------------------------------------------------


class Stage1Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    i: int
    p: int
    m: Literal["US", "KR", "MACRO"]
    a: bool
    s: list[str]
    k: str


class Stage1Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Stage1Item]


class LeaderOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ticker: str | None = None
    comment: str


class SectorOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    direction: Literal["issue", "up", "down"]
    market: Literal["US", "KR", "BOTH"]
    reason: str
    leaders: list[LeaderOut]
    us_view: str | None = None
    kr_impact: str | None = None


class ReportOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline5: list[str]
    signal_comments: list[str]
    sectors: list[SectorOut]
    ai_sector: str
    events_today: list[str]
    macro_notes: str
    disclaimer: str


# --- 검증용 --------------------------------------------------------------------

AI_SECTOR_MIN_CHARS = 300


def report_violations(out: ReportOut | "Report") -> list[str]:
    """Report 검증 규칙 위반 사유 목록(한국어). 비어 있으면 통과."""
    errs: list[str] = []
    if len(out.ai_sector) < AI_SECTOR_MIN_CHARS:
        errs.append(
            f"ai_sector는 {AI_SECTOR_MIN_CHARS}자 이상이어야 합니다 (현재 {len(out.ai_sector)}자)"
        )
    if len(out.headline5) != 5 or any(not h.strip() for h in out.headline5):
        errs.append(f"headline5는 비어 있지 않은 항목 정확히 5개여야 합니다 (현재 {len(out.headline5)}개)")
    if not out.sectors:
        errs.append("sectors는 최소 1개 이상이어야 합니다")
    for idx, sec in enumerate(out.sectors):
        label = f"sectors[{idx}] '{sec.name}'"
        if not 1 <= len(sec.leaders) <= 5:
            errs.append(f"{label}: leaders는 1~5개여야 합니다 (현재 {len(sec.leaders)}개)")
        if sec.market in ("US", "BOTH"):
            if not (sec.us_view or "").strip():
                errs.append(f"{label}: US/BOTH 섹터는 us_view(미국 관점)가 필요합니다")
            if not (sec.kr_impact or "").strip():
                errs.append(f"{label}: US/BOTH 섹터는 kr_impact(한국장 영향)가 필요합니다")
    return errs


class Report(ReportOut):
    """앱 측 검증 모델 — 모든 규칙 위반을 모아 한 번에 ValueError로 보고한다."""

    @model_validator(mode="after")
    def _check_rules(self) -> "Report":
        errs = report_violations(self)
        if errs:
            raise ValueError("; ".join(errs))
        return self
