"""stage1 — 기사 태깅(중요도·시장·AI 여부·섹터 코드·story_key)."""
from __future__ import annotations

from brief.analyze.client import CallCapExceeded, LLMClient, SkippedForDeadline
from brief.analyze.schemas import SECTOR_CODES, Stage1Result
from brief.collect.base import Article
from brief.config import STAGE1_MAX_CALLS, STAGE1_MODEL


class Stage1Degraded(Exception):
    """stage1 태깅 실패(캡 소진·데드라인) — 선별을 degrade 경로로 진행한다."""


def _sector_table() -> str:
    return "\n".join(f"- {code}: {name}" for code, name in SECTOR_CODES.items())


def build_stage1_prompt(articles: list[Article]) -> tuple[str, str]:
    """(system, user) 프롬프트. user는 `i | source | category | lang | title | summary[:200]` 목록."""
    system = (
        "당신은 금융 뉴스 태깅 도우미입니다. 아래 번호가 매겨진 기사 목록을 읽고 "
        "각 기사에 대해 Stage1Item을 하나씩 만들어 items 배열로 반환하세요. "
        "모든 기사 번호(i)를 빠짐없이 정확히 한 번씩 포함해야 합니다.\n\n"
        "필드 규칙:\n"
        "- i: 입력 목록의 기사 번호(정수, 그대로)\n"
        "- p: 중요도 1~5 (5=시장 전체에 영향, 1=사소함). 정수만.\n"
        "- m: 주 관련 시장. \"US\" | \"KR\" | \"MACRO\"(금리·환율·원자재·거시지표)\n"
        "- a: AI 관련 여부(true/false). AI·반도체·HBM·GPU·데이터센터·LLM·생성형 AI 등.\n"
        "- s: 섹터 코드 최대 3개(아래 코드표의 코드만 사용, 관련 순).\n"
        "- k: story_key. 같은 사건을 다루는 한국어/영어 기사가 동일한 값을 갖도록 "
        "짧은 ASCII 슬러그(소문자·숫자·하이픈, 예: nvidia-q2-earnings, fomc-hold)를 부여하세요. "
        "다른 사건이면 서로 다른 값을 쓰세요.\n\n"
        "섹터 코드표:\n" + _sector_table()
    )
    lines = ["i | source | category | lang | title | summary"]
    for i, a in enumerate(articles):
        summary = " ".join(a.summary.split())[:200]
        title = " ".join(a.title.split())
        lines.append(f"{i} | {a.source} | {a.category} | {a.lang} | {title} | {summary}")
    return system, "\n".join(lines)


def run_stage1(
    llm: LLMClient,
    articles: list[Article],
    *,
    deadline: float,
    model: str = STAGE1_MODEL,
) -> Stage1Result:
    system, user = build_stage1_prompt(articles)
    valid_ids = set(range(len(articles)))

    def check(result: Stage1Result) -> None:
        bad = [it.i for it in result.items if it.i not in valid_ids or not 1 <= it.p <= 5]
        if bad:
            raise ValueError(f"stage1 결과 오류: 존재하지 않는 i 또는 p 범위(1~5) 위반: {bad[:10]}")

    try:
        return llm.structured(
            "stage1",
            system=system,
            user=user,
            output_format=Stage1Result,
            model=model,
            deadline=deadline,
            max_calls=STAGE1_MAX_CALLS,
            budget_s=180,
            check=check,
        )
    except (CallCapExceeded, SkippedForDeadline) as e:
        raise Stage1Degraded(str(e)) from e
