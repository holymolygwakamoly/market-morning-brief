"""stage2 — 브리핑 본문 작성(CLI 구조화 출력) + 앱 측 Report 검증·재시도."""
from __future__ import annotations

from datetime import date
from typing import Any

from brief.analyze.client import LLMClient
from brief.analyze.schemas import Report, ReportOut, report_violations
from brief.analyze.select import Selected
from brief.collect.base import Quote
from brief.config import KST, STAGE2_MAX_CALLS, STAGE2_MODEL

DISCLAIMER = "본 보고서는 투자 조언이 아닙니다. 투자 판단과 책임은 투자자 본인에게 있습니다."

_SYSTEM = """당신은 한국 투자자를 위한 평일 아침 시장 브리핑 작성자입니다. 오늘은 {run_date}입니다.
입력으로 제공된 시세 표, 선별 기사(같은 사건의 한국어/영어 기사는 한 그룹으로 묶여 있음), 더벨 헤드라인만을 근거로
ReportOut JSON을 한국어로 작성하세요.

작성 규칙:
1. 섹터 > 종목. 개별 종목보다 섹터 흐름을 우선 서술합니다.
2. sectors: 각 섹터는 direction(issue=이슈/up=상승/down=하락)을 구분하고 reason(이유)을 쓰며 leaders(대표주)는 1~5개입니다.
   market은 US/KR/BOTH 중 하나. US 또는 BOTH 섹터는 us_view(미국 관점)와 kr_impact(한국장 영향)를 반드시 모두 채웁니다.
3. ai_sector: AI 섹터는 반드시 300자 이상으로 서술합니다. 오늘 AI 뉴스가 조용한 날에도 입력의 AI 관련 기사를 기준으로
   최근 흐름(반도체·HBM·데이터센터·빅테크 투자 등)을 300자 이상 정리하세요. 이 항목은 생략할 수 없습니다.
4. 입력에 제공된 시세·기사에 없는 수치(지수, 등락률, 목표가, 실적 수치 등)를 쓰지 마세요. 시세 수치는 시세 표의 값만 사용합니다.
   대표주(leaders)는 입력 기사에 등장한 종목만 사용하고, 기사에 없는 종목을 만들어내지 마세요.
5. headline5: 오늘의 핵심 헤드라인 정확히 5개(각 1문장).
6. signal_comments: 시세 표에 대한 해석 코멘트만 씁니다. 표의 수치를 반복해 나열하지 마세요.
7. events_today: 오늘 주목할 이벤트(실적 발표, 지표 발표, 회의 등). 입력에 근거가 없으면 빈 배열.
8. macro_notes: 금리·환율·원자재 등 거시 관점 요약.
9. disclaimer: 정확히 "{disclaimer}" 로 씁니다.
10. 분량 제한은 없습니다. 근거가 충분하면 상세히 쓰세요. 근거가 없는 내용은 쓰지 마세요."""


def _fmt_quotes(quotes: list[Quote]) -> str:
    if not quotes:
        return "(시세 없음)"
    lines = ["symbol | name | close | change_pct | asof(KST)"]
    for q in quotes:
        asof = q.asof.astimezone(KST).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{q.symbol} | {q.name} | {q.close:g} | {q.change_pct:+.2f}% | {asof}")
    return "\n".join(lines)


def _fmt_groups(selected: Selected) -> str:
    if not selected.groups:
        return "(선별 기사 없음)"
    out: list[str] = []
    for n, (key, arts) in enumerate(selected.groups.items(), 1):
        out.append(f"[{n}] story_key={key}")
        for a in arts:
            pub = a.published_at.astimezone(KST).strftime("%m-%d %H:%M")
            summary = " ".join(a.summary.split())[:300]
            out.append(f"  - ({a.lang}/{a.category}) {a.title} — {a.source}, {pub}, {a.url}")
            if summary:
                out.append(f"    요약: {summary}")
    return "\n".join(out)


def _fmt_thebell(selected: Selected) -> str:
    if not selected.thebell:
        return "(더벨 헤드라인 없음)"
    return "\n".join(f"- {a.title}" for a in selected.thebell)


def build_stage2_prompt(
    selected: Selected,
    quotes: list[Quote],
    run_date: date,
    warnings_from_prev_attempt: str | None = None,
) -> tuple[str, str]:
    """(system, user) 프롬프트."""
    system = _SYSTEM.format(run_date=run_date.isoformat(), disclaimer=DISCLAIMER)
    parts = [
        "## 시세 표\n" + _fmt_quotes(quotes),
        "## 선별 기사 (story_key 그룹별, KO/EN 링크 병기)\n" + _fmt_groups(selected),
        "## 더벨 헤드라인 (제목만)\n" + _fmt_thebell(selected),
    ]
    if selected.degraded:
        parts.append("## 참고\n기사 태깅 단계가 실패하여 최신순·키워드 기준으로 선별된 기사입니다.")
    if warnings_from_prev_attempt:
        parts.append(
            "## 이전 출력 검증 실패\n"
            f"이전 출력이 다음 검증에 실패했습니다: {warnings_from_prev_attempt}\n"
            "위 항목을 수정해서 다시 작성하세요."
        )
    return system, "\n\n".join(parts)


def _relaxed_report(out: ReportOut) -> Report:
    """ai_sector 길이 규칙만 미달인 출력을 검증 없이 Report로 감싼다."""
    return Report.model_construct(**{f: getattr(out, f) for f in ReportOut.model_fields})


def run_stage2(
    llm: LLMClient,
    selected: Selected,
    quotes: list[Quote],
    run_date: date,
    *,
    deadline: float,
    model: str = STAGE2_MODEL,
) -> tuple[Report, list[str]]:
    """최대 STAGE2_MAX_CALLS회 시도. 마지막 시도에서 ai_sector 길이만 미달이면 완화 반환 + warnings."""
    base_calls = llm.calls.get("stage2", 0)
    state: dict[str, Any] = {"prev": None}

    def fn(timeout: float) -> tuple[Report, list[str]]:
        attempt = llm.calls.get("stage2", 0) - base_calls
        system, user = build_stage2_prompt(selected, quotes, run_date, state["prev"])
        out = llm.structured_once(
            "stage2", system=system, user=user, output_format=ReportOut, model=model, timeout=timeout
        )
        errs = report_violations(out)
        if not errs:
            return Report.model_validate(out.model_dump()), []
        if attempt >= STAGE2_MAX_CALLS and all(e.startswith("ai_sector") for e in errs):
            return _relaxed_report(out), ["ai_sector_short"]
        state["prev"] = "; ".join(errs)
        raise ValueError(state["prev"])

    return llm.call("stage2", fn, max_calls=STAGE2_MAX_CALLS, deadline=deadline, min_seconds=180, budget_s=360)
