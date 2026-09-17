"""stage2(v4) — 토픽 보고서 6종 생성: 거시경제 / 섹터별 주요뉴스 / 미국 / 유럽 / 한국 / 중국 (PLAN §11.4).

각 토픽은 `LLMClient.call("stage2:<topic>")` 아래 최대 `TOPIC_MAX_CALLS`회 시도하며 서로 독립적으로 실패한다.
입력 = 해당 지역 시장 데이터 표(지수·구성종목 상위·섹터 ETF) + 선별 기사(story_key 그룹) + (kr·sectors) 더벨 헤드라인.
분량 규칙(`[length]`)만 마지막 시도에서 미달이면 완화 게시 + warnings.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel

from brief.analyze.client import CallCapExceeded, LLMClient, SkippedForDeadline
from brief.analyze.schemas import (
    LENGTH_PREFIX,
    SectorReportOut,
    TopicReportOut,
    sector_report_violations,
    topic_report_violations,
)
from brief.analyze.select import TOPIC_NAMES, Selected
from brief.config import KST, STAGE2_MODEL
from brief.market.base import ConstituentTable, IndexQuote, MarketSnapshot

logger = logging.getLogger(__name__)

DISCLAIMER = "본 보고서는 투자 조언이 아닙니다. 투자 판단과 책임은 투자자 본인에게 있습니다."
TOPIC_MAX_CALLS = 2
TOPIC_BUDGET_S = 480
TOPIC_MIN_SECONDS = 180
MAX_PARALLEL = 3

_COMMON_RULES = """
공통 작성 규칙:
1. 한국어. 분량 상한 없음 — 근거가 있는 내용은 최대한 상세히 쓰되, 근거 없는 내용은 쓰지 않습니다.
2. 입력에 제공된 시세 표·기사에 없는 수치(지수 값, 등락률, 목표가, 실적 수치)를 만들어 쓰지 마세요. 수치는 표의 값만 인용합니다.
3. leaders(대표주·주도주)는 입력 기사나 구성종목 표에 등장한 종목만 사용합니다.
4. headlines: 이 보고서에서 가장 중요한 한 줄 요약 1~3개, importance 1~5(5=시장 전체에 영향). 홈 화면 "오늘의 핵심"에 쓰입니다.
5. events_today: 오늘·이번 주 주목할 이벤트(지표·실적·회의). 근거가 없으면 빈 배열.
6. data_caveats: 데이터 한계(예: 무료 소스에 수급 데이터 없음, 장중 값 등)를 짧게 나열. 없으면 빈 배열.
7. disclaimer: 정확히 "{disclaimer}" 로 씁니다.
8. 같은 사건의 한국어/영어/중국어 기사는 한 그룹으로 묶여 있습니다. 출처 언어와 무관하게 한국 투자자 관점으로 해석합니다."""

_TOPIC_SYSTEM: dict[str, str] = {
    "macro": """당신은 한국 투자자를 위한 리서치 데스크의 거시경제 애널리스트입니다. 기준일은 {run_date}입니다.
입력(환율·금리·원자재·지수 표, 중앙은행·거시 기사)을 근거로 TopicReportOut JSON을 작성하세요.
sections는 아래 순서·제목으로 구성하고 각 section body는 200자 이상의 서술(문단)로, bullets에는 핵심 포인트를 넣습니다:
 1) "금리·채권" — 미국 국채(3개월·5년·10년) 수준과 변화, 커브, 시장의 금리 경로 기대
 2) "환율" — 달러인덱스·원/달러·엔·유로·위안의 움직임과 배경, 한국 수출·수급에 주는 함의
 3) "원자재" — 유가(WTI·브렌트)·금·구리와 그 배경(공급·지정학·수요)
 4) "중앙은행" — Fed·ECB·BoE·BOJ·한국은행·인민은행의 발표·발언·일정
 5) "경제지표" — 어제 발표된 지표의 결과·해석과 오늘·이번 주 발표 예정
 6) "지정학·정책" — 전쟁·관세·제재·선거 등 시장에 영향을 주는 정치 이슈
 7) "자산별 함의" — 주식(미국·한국)·채권·환율·원자재 관점에서 오늘 매매에 참고할 시사점
overview는 위 내용을 3~5문장으로 요약합니다.""" + _COMMON_RULES,
    "us": """당신은 한국 투자자를 위한 리서치 데스크의 미국 주식 애널리스트입니다. 기준일은 {run_date}입니다.
입력(미국 지수·섹터 ETF·구성종목 상위/하위 표, 미국 시장 기사)을 근거로 직전 세션의 미국 시장을 정리한 TopicReportOut JSON을 작성하세요.
sections는 아래 순서·제목으로 구성하고 각 body는 200자 이상:
 1) "지수 흐름" — S&P500·나스닥·다우·러셀·반도체지수·VIX의 마감과 세션 흐름, 배경
 2) "시장 폭과 거래" — 상승/하락 종목 수, 거래대금 상위 종목, 변동성(VIX)에서 읽히는 수급·심리 (무료 소스에는 기관·개인 수급 데이터가 없음을 명시)
 3) "섹터 동향" — 섹터 ETF 등락 순위와 이유, 강한 섹터·약한 섹터
 4) "주도주와 급등락" — 구성종목 표의 상위 상승·하락 종목과 기사에 나온 이유 (leaders에도 반영)
 5) "실적·기업 뉴스" — 주요 기업 실적·가이던스·M&A·규제
 6) "오늘 이벤트" — 오늘 예정된 실적·지표·연준 일정
 7) "한국장 영향" — 미국 세션이 오늘 한국장(반도체·2차전지·자동차·조선 등)에 주는 시사점
overview는 3~5문장 요약.""" + _COMMON_RULES,
    "eu": """당신은 한국 투자자를 위한 리서치 데스크의 유럽 주식 애널리스트입니다. 기준일은 {run_date}입니다.
입력(유럽 지수 표, 유럽 시장·ECB·BoE 기사)을 근거로 직전 세션의 유럽 시장을 정리한 TopicReportOut JSON을 작성하세요.
sections는 아래 순서·제목으로 구성하고 각 body는 200자 이상:
 1) "지수 흐름" — 스톡스600·유로스톡스50·DAX·FTSE100·CAC40의 마감과 배경
 2) "섹터·주도주" — 강했던/약했던 업종과 기사에 등장한 대표 종목 (leaders에 반영)
 3) "ECB·BoE·거시" — 통화정책·물가·성장 관련 발표와 시장 반응
 4) "주요 뉴스" — 기업·정책·지정학 뉴스 정리
 5) "미국·한국 영향" — 유럽 세션이 미국장·한국장에 주는 시사점(수출·환율·업종 연관)
overview는 3~5문장 요약. 유럽 뉴스 소스가 제한적이면 data_caveats에 명시.""" + _COMMON_RULES,
    "kr": """당신은 한국 투자자를 위한 리서치 데스크의 국내 주식 애널리스트입니다. 기준일은 {run_date}입니다.
입력(코스피·코스닥 지수, 구성종목 상위/하위·거래대금 표, 국내 기사, 더벨 헤드라인)을 근거로 직전 세션의 한국 시장과 오늘 장 전망 참고를 정리한 TopicReportOut JSON을 작성하세요.
sections는 아래 순서·제목으로 구성하고 각 body는 200자 이상:
 1) "지수 흐름" — 코스피·코스닥 마감과 세션 흐름, 배경(미국 세션·환율 연계)
 2) "수급" — 외국인·기관·개인 매매 동향은 기사에 언급된 내용만 근거로 서술(무료 소스에 KRX 투자자별 데이터가 없음을 명시), 거래대금 상위 종목·시장 폭
 3) "업종 동향" — 강한 업종·약한 업종과 이유
 4) "주도주와 급등락" — 구성종목 표의 상위 상승·하락 종목과 기사 속 이유 (leaders에 반영)
 5) "주요 뉴스·더벨" — 기업·정책 뉴스와 더벨 헤드라인 중 중요한 것(제목만 있으므로 과도한 해석 금지)
 6) "오늘 이벤트" — 공시·지표·정책 일정
 7) "오늘 장 참고" — 미국 세션·환율·원자재를 반영한 오늘 한국장 참고 포인트(매매 추천이 아닌 시나리오)
overview는 3~5문장 요약.""" + _COMMON_RULES,
    "cn": """당신은 한국 투자자를 위한 리서치 데스크의 중국·홍콩 주식 애널리스트입니다. 기준일은 {run_date}입니다.
입력(상해·심천·항셍·닛케이 지수 표, 중국·아시아 기사)을 근거로 직전 세션의 중국·홍콩 시장을 정리한 TopicReportOut JSON을 작성하세요.
sections는 아래 순서·제목으로 구성하고 각 body는 200자 이상:
 1) "지수 흐름" — 상해종합·심천성분·항셍(·닛케이)의 마감과 배경
 2) "정책·유동성" — 인민은행·국무원·규제 당국 발표, 부양책, 북향자금(기사에 언급된 경우만)
 3) "섹터·주도주" — 강한/약한 업종과 기사에 등장한 대표 종목 (leaders에 반영)
 4) "주요 뉴스" — 기업·무역·지정학 뉴스 정리
 5) "한국 영향" — 중국 노출 업종(화장품·2차전지 소재·철강·화학·반도체 장비 등)과 원화·위안 연동 관점의 시사점
overview는 3~5문장 요약. 중국어 기사는 제목·요약을 바탕으로 해석하되 확실하지 않은 내용은 쓰지 않습니다.""" + _COMMON_RULES,
    "sectors": """당신은 한국 투자자를 위한 리서치 데스크의 섹터 전략가입니다. 기준일은 {run_date}입니다.
입력(전 지역 지수·섹터 ETF 표, 미국·한국 구성종목 상위/하위 표, 전 지역 주요 기사)을 근거로 SectorReportOut JSON을 작성하세요.
- sectors: 오늘 이슈가 있는 섹터, 상승 섹터, 하락 섹터를 direction(issue/up/down)으로 구분해 나열(모든 섹터를 다룰 필요 없음, 이슈 있는 것만 5~12개).
  각 섹터에 markets(영향 시장: US/EU/KR/CN), reason(왜 움직였는지 100자 이상), news(관련 세계 주요 뉴스 요약), leaders(대표주 1~5, 입력 등장분만), etf_note(섹터 ETF 등락이 있으면).
  전쟁 재개·관세·제재 같은 세계 뉴스는 영향 섹터(방산·에너지·항공·해운 등)에 반영합니다.
- ai_sector: AI 섹터는 매일 필수, 300자 이상. 조용한 날에도 입력의 AI·반도체·데이터센터 기사를 근거로 최근 흐름·밸류에이션·수급을 정리합니다.
- cross_events: 여러 섹터에 걸친 사건(지정학·정책·원자재)을 section(heading/body 200자 이상/bullets)으로 정리.
overview는 3~5문장 요약.""" + _COMMON_RULES,
}

TOPIC_SCHEMAS: dict[str, type[BaseModel]] = {t: TopicReportOut for t in ("macro", "us", "eu", "kr", "cn")}
TOPIC_SCHEMAS["sectors"] = SectorReportOut


# --- 데이터 표 포맷 --------------------------------------------------------------


def _fmt_num(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "-"
    if abs(v) >= 1e12:
        return f"{v / 1e12:.2f}조" if digits else f"{v / 1e12:.0f}조"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.0f}억"
    if abs(v) >= 1e4:
        return f"{v:,.0f}"
    return f"{v:.{digits}f}"


def fmt_indices(quotes: list[IndexQuote], title: str) -> str:
    if not quotes:
        return f"## {title}\n(데이터 없음)"
    lines = [f"## {title}", "name | symbol | close | 전일 대비 | change_pct | session_date"]
    for q in quotes:
        lines.append(f"{q.name} | {q.symbol} | {_fmt_num(q.close)} | {_fmt_num(q.change_abs)} | {q.change_pct:+.2f}% | {q.session_date}")
    return "\n".join(lines)


def fmt_constituents(table: ConstituentTable, *, top: int = 10) -> str:
    rows = [r for r in table.rows if r.change_pct is not None]
    if not rows:
        return f"## {table.index_name} 구성종목\n(데이터 없음: {table.error or '-'})"
    up = sum(1 for r in rows if (r.change_pct or 0) > 0)
    down = sum(1 for r in rows if (r.change_pct or 0) < 0)
    head = [f"## {table.index_name} 구성종목 ({len(rows)}/{table.total}종목, 기준 {table.session_date}{', ' + table.note if table.note else ''})",
            f"상승 {up} / 하락 {down} / 보합 {len(rows) - up - down}"]
    by_chg = sorted(rows, key=lambda r: r.change_pct or 0, reverse=True)
    by_val = sorted(rows, key=lambda r: r.value or 0, reverse=True)

    def block(name: str, rs: list) -> list[str]:
        out = [f"### {name}", "ticker | name | sector | close | change_pct | 거래대금(추정) | 시가총액"]
        for r in rs:
            out.append(f"{r.ticker} | {r.name} | {r.sector or '-'} | {_fmt_num(r.close)} | {r.change_pct:+.2f}% | {_fmt_num(r.value, 0)} | {_fmt_num(r.market_cap, 0)}")
        return out

    return "\n".join(head + block(f"상승 상위 {top}", by_chg[:top]) + block(f"하락 상위 {top}", by_chg[-top:][::-1]) + block(f"거래대금 상위 {top}", by_val[:top]))


def market_context(topic: str, market: MarketSnapshot) -> str:
    by_group: dict[str, list[IndexQuote]] = {}
    for q in market.indices:
        by_group.setdefault(q.group or q.region, []).append(q)
    parts: list[str] = []

    def add_groups(*names: str) -> None:
        for n in names:
            if n in by_group:
                parts.append(fmt_indices(by_group[n], n))

    def add_tables(*keys: str) -> None:
        for k in keys:
            if k in market.constituents:
                parts.append(fmt_constituents(market.constituents[k]))

    if topic == "macro":
        add_groups("환율", "금리", "원자재", "미국", "유럽", "한국", "중국·아시아")
    elif topic == "us":
        add_groups("미국", "섹터ETF", "금리", "환율")
        add_tables("us_sp500", "us_ndx")
    elif topic == "eu":
        add_groups("유럽", "환율", "미국")
    elif topic == "kr":
        add_groups("한국", "미국", "환율", "원자재")
        add_tables("kr_kospi", "kr_kosdaq")
    elif topic == "cn":
        add_groups("중국·아시아", "환율", "원자재")
    elif topic == "sectors":
        add_groups("섹터ETF", "미국", "한국", "유럽", "중국·아시아", "원자재")
        add_tables("us_sp500", "kr_kospi")
    if market.errors:
        parts.append("## 시장 데이터 경고\n" + "\n".join(f"- {e}" for e in market.errors[:10]))
    return "\n\n".join(parts) if parts else "(시장 데이터 없음)"


# --- 기사 포맷 -----------------------------------------------------------------


def fmt_groups(selected: Selected) -> str:
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


def fmt_thebell(selected: Selected) -> str:
    if not selected.thebell:
        return "(더벨 헤드라인 없음)"
    return "\n".join(f"- {a.title}" for a in selected.thebell)


def build_topic_prompt(
    topic: str,
    selected: Selected,
    market: MarketSnapshot,
    run_date: date,
    prev_errors: str | None = None,
) -> tuple[str, str]:
    system = _TOPIC_SYSTEM[topic].format(run_date=run_date.isoformat(), disclaimer=DISCLAIMER)
    parts = [
        "# 시장 데이터\n" + market_context(topic, market),
        "# 선별 기사 (story_key 그룹별)\n" + fmt_groups(selected),
    ]
    if selected.thebell:
        parts.append("# 더벨 헤드라인 (제목만)\n" + fmt_thebell(selected))
    if selected.degraded:
        parts.append("# 참고\n기사 태깅 단계가 실패하여 최신순·키워드 기준으로 선별된 기사입니다.")
    if prev_errors:
        parts.append("# 이전 출력 검증 실패\n이전 출력이 다음 검증에 실패했습니다: " + prev_errors + "\n위 항목을 수정해서 다시 작성하세요.")
    return system, "\n\n".join(parts)


# --- 실행 ----------------------------------------------------------------------


@dataclass
class TopicResult:
    topic: str
    report: BaseModel | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.report is not None


def _violations(topic: str, out: BaseModel) -> list[str]:
    if topic == "sectors":
        return sector_report_violations(out, DISCLAIMER)  # type: ignore[arg-type]
    return topic_report_violations(out, DISCLAIMER)  # type: ignore[arg-type]


def run_topic(
    llm: LLMClient,
    topic: str,
    selected: Selected,
    market: MarketSnapshot,
    run_date: date,
    *,
    deadline: float,
    model: str = STAGE2_MODEL,
    max_calls: int = TOPIC_MAX_CALLS,
) -> TopicResult:
    stage = f"stage2:{topic}"
    res = TopicResult(topic=topic)
    state: dict[str, Any] = {"prev": None}
    schema = TOPIC_SCHEMAS[topic]

    def fn(timeout: float) -> tuple[BaseModel, list[str]]:
        res.attempts += 1
        system, user = build_topic_prompt(topic, selected, market, run_date, state["prev"])
        out = llm.structured_once(stage, system=system, user=user, output_format=schema, model=model, timeout=timeout)
        errs = _violations(topic, out)
        if not errs:
            return out, []
        if res.attempts >= max_calls and all(e.startswith(LENGTH_PREFIX) for e in errs):
            return out, ["length_short"]
        state["prev"] = "; ".join(errs)
        raise ValueError(state["prev"])

    try:
        report, warnings = llm.call(stage, fn, max_calls=max_calls, deadline=deadline, min_seconds=TOPIC_MIN_SECONDS, budget_s=TOPIC_BUDGET_S)
        res.report, res.warnings = report, warnings
    except (CallCapExceeded, SkippedForDeadline) as e:
        res.error = f"{type(e).__name__}: {str(e)[:300]}"
        logger.warning("topic %s failed: %s", topic, res.error)
    return res


def run_topics(
    llm: LLMClient,
    selected: dict[str, Selected],
    market: MarketSnapshot,
    run_date: date,
    *,
    deadline: float,
    topics: tuple[str, ...] | None = None,
    max_parallel: int = MAX_PARALLEL,
    model: str = STAGE2_MODEL,
) -> dict[str, TopicResult]:
    names = tuple(topics or tuple(selected.keys()))
    with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        futures = {t: ex.submit(run_topic, llm, t, selected[t], market, run_date, deadline=deadline, model=model) for t in names}
        return {t: f.result() for t, f in futures.items()}


# --- 홈 "오늘의 핵심 5" ------------------------------------------------------------


def _norm(s: str) -> str:
    return re.sub(r"[^\w]+", "", s.lower())


def top_headlines(results: dict[str, TopicResult], n: int = 5) -> list[dict]:
    """토픽별 headlines에서 n개. 1차: 토픽마다 최상위 1개(중요도순), 2차: 남은 것 중요도순. 비슷한 문장(bigram Dice ≥ 0.5)은 제외."""
    from brief.dedupe import bigram_dice  # noqa: PLC0415

    order = {t: i for i, t in enumerate(("macro", "us", "kr", "sectors", "eu", "cn"))}
    per_topic: dict[str, list] = {}
    for topic, r in results.items():
        if r.report:
            per_topic[topic] = sorted(getattr(r.report, "headlines", []), key=lambda h: h.importance, reverse=True)
    rounds: list[list[tuple[int, int, str, str]]] = [[], []]
    for topic, hs in per_topic.items():
        for k, h in enumerate(hs[:2]):
            rounds[k].append((h.importance, -order.get(topic, 9), topic, h.text.strip()))
    out: list[dict] = []
    seen: list[str] = []
    for cands in rounds:
        for imp, _, topic, text in sorted(cands, reverse=True):
            key = _norm(text)
            if not text or any(bigram_dice(key, s) >= 0.5 for s in seen):
                continue
            seen.append(key)
            out.append({"text": text, "importance": imp, "topic": topic, "topic_name": TOPIC_NAMES.get(topic, topic)})
            if len(out) >= n:
                return out
    return out
