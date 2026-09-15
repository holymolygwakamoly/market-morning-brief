"""Step 3 분석 계층 테스트 — SDK는 FakeAnthropic으로 모킹(네트워크 없음)."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from brief.analyze.client import CallCapExceeded, LLMClient, SkippedForDeadline
from brief.analyze.schemas import Report, ReportOut, Stage1Item, Stage1Result
from brief.analyze.select import select_for_stage2
from brief.analyze.stage1_tag import Stage1Degraded, run_stage1
from brief.analyze.stage2_write import run_stage2
from brief.collect.base import Article, Quote

FIXTURES = Path(__file__).parent / "fixtures"
VALID_REPORT = json.loads((FIXTURES / "report_valid.json").read_text(encoding="utf-8"))
FAR = time.monotonic() + 3600


# --- helpers -----------------------------------------------------------------


def _article(i: int, *, source="연합", category="KR", lang="ko", title=None, minutes_ago=0) -> Article:
    return Article(
        title=title or f"기사 {i}",
        url=f"https://ex.com/{source}/{i}",
        published_at=datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago),
        source=source,
        category=category,
        lang=lang,
        summary=f"요약 {i}",
    )


def _quote() -> Quote:
    return Quote(symbol="^GSPC", name="S&P 500", close=6500.12, change_pct=0.45,
                 asof=datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc))


def _msg(parsed, inp=1000, out=500):
    return SimpleNamespace(parsed_output=parsed, usage=SimpleNamespace(input_tokens=inp, output_tokens=out))


class FakeAnthropic:
    """`.messages.parse` / `.messages.stream` / `.with_options` 모킹. responses는 값 또는 예외."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.parse_kwargs: list[dict] = []
        self.stream_kwargs: list[dict] = []
        self.timeouts: list[float] = []
        self.messages = self

    def with_options(self, **kw):
        self.timeouts.append(kw.get("timeout"))
        return self

    def _next(self):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def parse(self, **kwargs):
        self.parse_kwargs.append(kwargs)
        return self._next()

    @contextmanager
    def stream(self, **kwargs):
        self.stream_kwargs.append(kwargs)
        msg = self._next()
        yield SimpleNamespace(get_final_message=lambda: msg)


def _llm(fake, sleeps=None):
    return LLMClient(client=fake, sleep=(sleeps.append if sleeps is not None else lambda s: None))


def _report_out(**overrides) -> ReportOut:
    return ReportOut.model_validate({**VALID_REPORT, **overrides})


def _selected():
    arts = [_article(i, category=c) for i, c in enumerate(["US", "KR", "MACRO"])]
    return select_for_stage2(arts, None)


# --- schema (전달용) ---------------------------------------------------------------

_FORBIDDEN = {"minLength", "maxLength", "minItems", "maxItems"}


def _walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            assert k not in _FORBIDDEN, f"{path}.{k}"
            if isinstance(v, dict) and v.get("type") == "object":
                assert v.get("additionalProperties") is False, f"{path}.{k} additionalProperties"
            _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}[{i}]")


@pytest.mark.parametrize("model", [Stage1Result, ReportOut])
def test_transport_schema_has_no_length_constraints_and_forbids_extra(model):
    schema = model.model_json_schema()
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    for d in schema.get("$defs", {}).values():
        if d.get("type") == "object":
            assert d["additionalProperties"] is False
    _walk(schema)


# --- Report validators ---------------------------------------------------------------


def test_report_valid_fixture_passes():
    r = Report.model_validate(VALID_REPORT)
    assert len(r.headline5) == 5 and len(r.ai_sector) >= 300


def test_report_ai_sector_299_fails():
    with pytest.raises(ValidationError, match="ai_sector"):
        Report.model_validate({**VALID_REPORT, "ai_sector": "가" * 299})


def test_report_headline5_of_4_fails():
    with pytest.raises(ValidationError, match="headline5"):
        Report.model_validate({**VALID_REPORT, "headline5": VALID_REPORT["headline5"][:4]})


def test_report_us_sector_without_kr_impact_fails():
    sectors = json.loads(json.dumps(VALID_REPORT["sectors"]))
    sectors[0]["market"] = "US"
    sectors[0]["kr_impact"] = None
    with pytest.raises(ValidationError, match="kr_impact"):
        Report.model_validate({**VALID_REPORT, "sectors": sectors})


def test_report_six_leaders_fails():
    sectors = json.loads(json.dumps(VALID_REPORT["sectors"]))
    sectors[0]["leaders"] = [sectors[0]["leaders"][0]] * 6
    with pytest.raises(ValidationError, match="leaders"):
        Report.model_validate({**VALID_REPORT, "sectors": sectors})


def test_report_collects_all_failures_at_once():
    with pytest.raises(ValidationError) as ei:
        Report.model_validate({**VALID_REPORT, "ai_sector": "짧음", "headline5": ["a"], "sectors": []})
    msg = str(ei.value)
    assert "ai_sector" in msg and "headline5" in msg and "sectors" in msg


# --- stage1 -----------------------------------------------------------------------


def _stage1_result(n):
    return Stage1Result(items=[Stage1Item(i=i, p=3, m="KR", a=False, s=["OTHER"], k=f"s{i}") for i in range(n)])


def test_stage1_call_kwargs_sonnet_disables_thinking():
    arts = [_article(i) for i in range(3)]
    fake = FakeAnthropic([_msg(_stage1_result(3))])
    res = run_stage1(_llm(fake), arts, deadline=FAR, model="claude-sonnet-5")
    assert isinstance(res, Stage1Result) and len(res.items) == 3
    kw = fake.parse_kwargs[0]
    assert kw["output_format"] is Stage1Result
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["max_tokens"] == 16000 and kw["model"] == "claude-sonnet-5"
    assert "format" not in kw.get("output_config", {})
    assert "0 | 연합 | KR | ko | 기사 0" in kw["messages"][0]["content"]


def test_stage1_call_kwargs_haiku_omits_thinking():
    fake = FakeAnthropic([_msg(_stage1_result(2))])
    run_stage1(_llm(fake), [_article(i) for i in range(2)], deadline=FAR, model="claude-haiku-4-5")
    assert "thinking" not in fake.parse_kwargs[0]


def test_stage1_invalid_index_triggers_retry_then_success():
    bad = Stage1Result(items=[Stage1Item(i=99, p=3, m="KR", a=False, s=[], k="x")])
    fake = FakeAnthropic([_msg(bad), _msg(_stage1_result(2))])
    llm = _llm(fake)
    res = run_stage1(llm, [_article(i) for i in range(2)], deadline=FAR)
    assert len(res.items) == 2 and llm.calls["stage1"] == 2


def test_stage1_always_raising_hits_cap_of_2_then_degraded():
    fake = FakeAnthropic([RuntimeError("api down")] * 5)
    llm = _llm(fake)
    with pytest.raises(Stage1Degraded):
        run_stage1(llm, [_article(0)], deadline=FAR)
    assert llm.calls["stage1"] == 2 and len(fake.parse_kwargs) == 2


# --- stage2 -----------------------------------------------------------------------


def test_stage2_call_kwargs():
    fake = FakeAnthropic([_msg(_report_out())])
    report, warnings = run_stage2(_llm(fake), _selected(), [_quote()], date(2026, 9, 18), deadline=FAR)
    assert isinstance(report, Report) and warnings == []
    kw = fake.stream_kwargs[0]
    assert kw["output_format"] is ReportOut
    assert kw["output_config"] == {"effort": "medium"}
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["max_tokens"] == 16000
    user = kw["messages"][0]["content"]
    assert "^GSPC" in user and "6500.12" in user and "이전 출력" not in user


def test_stage2_invalid_then_valid_retries_with_reasons():
    bad = _report_out(ai_sector="짧은 AI", headline5=VALID_REPORT["headline5"][:4])
    fake = FakeAnthropic([_msg(bad), _msg(_report_out())])
    llm = _llm(fake)
    report, warnings = run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=FAR)
    assert isinstance(report, Report) and warnings == []
    assert llm.calls["stage2"] == 2 and len(fake.stream_kwargs) == 2
    second = fake.stream_kwargs[1]["messages"][0]["content"]
    assert "이전 출력이 다음 검증에 실패했습니다" in second
    assert "ai_sector" in second and "headline5" in second


def test_stage2_always_invalid_non_ai_rule_hits_cap_of_3():
    bad = _report_out(headline5=["하나"])
    fake = FakeAnthropic([_msg(bad)] * 5)
    llm = _llm(fake)
    with pytest.raises(CallCapExceeded):
        run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=FAR)
    assert llm.calls["stage2"] == 3 and len(fake.stream_kwargs) == 3


def test_stage2_ai_sector_only_failure_on_last_attempt_returns_relaxed():
    short = _report_out(ai_sector="AI 섹터 조용")
    fake = FakeAnthropic([_msg(short)] * 3)
    llm = _llm(fake)
    report, warnings = run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=FAR)
    assert warnings == ["ai_sector_short"]
    assert isinstance(report, Report) and report.ai_sector == "AI 섹터 조용"
    assert len(report.headline5) == 5 and llm.calls["stage2"] == 3


def test_stage2_api_error_then_valid_recovers():
    fake = FakeAnthropic([RuntimeError("overloaded"), _msg(_report_out())])
    sleeps: list[float] = []
    llm = _llm(fake, sleeps)
    report, _ = run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=FAR)
    assert isinstance(report, Report) and llm.calls["stage2"] == 2 and sleeps == [5]


# --- deadline ---------------------------------------------------------------------


def test_deadline_too_close_skips_without_calls():
    fake = FakeAnthropic([_msg(_report_out())])
    llm = _llm(fake)
    with pytest.raises(SkippedForDeadline):
        run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=time.monotonic() + 10)
    assert llm.calls.get("stage2", 0) == 0 and fake.stream_kwargs == []
    with pytest.raises(Stage1Degraded):
        run_stage1(llm, [_article(0)], deadline=time.monotonic() + 10)
    assert llm.calls.get("stage1", 0) == 0 and fake.parse_kwargs == []


def test_call_timeout_derived_from_remaining():
    fake = FakeAnthropic([_msg(_stage1_result(1))])
    run_stage1(_llm(fake), [_article(0)], deadline=time.monotonic() + 100)
    assert 35 <= fake.timeouts[0] <= 40  # min(budget 180, remaining-60)


def test_call_timeout_capped_by_stage_budget():
    fake = FakeAnthropic([_msg(_stage1_result(1))])
    run_stage1(_llm(fake), [_article(0)], deadline=time.monotonic() + 800)
    assert 175 <= fake.timeouts[0] <= 180


# --- select -------------------------------------------------------------------------


def _pool():
    arts = []
    n = 0
    for cat, count in (("US", 30), ("KR", 30), ("MACRO", 15)):
        for j in range(count):
            arts.append(_article(n, category=cat, source=f"src{cat}", minutes_ago=n))
            n += 1
    for j in range(35):
        arts.append(_article(n, source="더벨", title=f"더벨 딜 {j}", minutes_ago=n))
        n += 1
    for j in range(12):
        arts.append(_article(n, category="KR", title=f"엔비디아 HBM 뉴스 {j}", minutes_ago=500 + n))
        n += 1
    return arts


def test_select_degraded_path():
    arts = _pool()
    sel = select_for_stage2(arts, None)
    assert sel.degraded is True and sel.tags is None
    assert len(sel.thebell) == 30 and all(a.source == "더벨" for a in sel.thebell)
    assert not any(a.source == "더벨" for a in sel.articles)
    cats = {c: sum(1 for a in sel.articles if a.category == c) for c in ("US", "KR", "MACRO")}
    assert cats["US"] == 25 and cats["MACRO"] == 10
    ai = [a for a in sel.articles if "엔비디아" in a.title]
    assert 1 <= len(ai) <= 10 and cats["KR"] == 25 + len(ai)
    assert sel.groups and sum(len(v) for v in sel.groups.values()) == len(sel.articles)


def test_select_with_tags_uses_market_and_importance():
    arts = _pool()
    items = []
    for i, a in enumerate(arts):
        if a.source == "더벨":
            continue
        m = "MACRO" if a.category == "US" else a.category  # US 기사를 MACRO로 태깅 → category가 아닌 태그 기준 확인
        items.append(Stage1Item(i=i, p=5 if i % 7 == 0 else 2, m=m, a="엔비디아" in a.title or i < 20,
                                s=["SEMI"], k=f"story{i % 40}"))
    sel = select_for_stage2(arts, Stage1Result(items=items))
    assert sel.degraded is False and sel.tags is not None and len(sel.thebell) == 30
    tag_of = {arts.index(a): sel.tags[arts.index(a)] for a in sel.articles}
    markets = {}
    for t in tag_of.values():
        markets[t.m] = markets.get(t.m, 0) + 1
    assert "US" not in markets and markets["MACRO"] >= 10 and markets["KR"] >= 25
    assert len(sel.articles) == 25 + 10 + 15  # KR 25 + MACRO 10 + is_ai 15
    assert all(it.i in tag_of for it in items if it.p == 5)  # 중요도 5 우선 선별
    assert any(len(v) > 1 for v in sel.groups.values())  # story_key 그룹핑


# --- usage / cost ---------------------------------------------------------------------


def test_record_usage_costs_and_summary():
    llm = _llm(FakeAnthropic([]))
    llm.record_usage("stage1", "claude-sonnet-5", _msg(None, 32_000, 6_000))
    assert llm.usages[-1].cost_usd == pytest.approx(0.124)
    llm.record_usage("stage1", "claude-haiku-4-5", _msg(None, 32_000, 6_000))
    assert llm.usages[-1].cost_usd == pytest.approx(0.062)
    llm.record_usage("stage2", "claude-unknown", _msg(None, 1_000_000, 0))
    assert llm.usages[-1].cost_usd == pytest.approx(2.0)  # 미지 모델 → Sonnet 단가
    s = llm.summary()
    assert s["total_cost_usd"] == pytest.approx(2.186)
    assert s["stages"]["stage1"]["input_tokens"] == 64_000
    assert s["total_input_tokens"] == 1_064_000 and s["total_output_tokens"] == 12_000
    assert s["warnings"] == ["cost_over_soft_cap"]


def test_cost_soft_cap_warning():
    llm = _llm(FakeAnthropic([]))
    llm.record_usage("stage2", "claude-sonnet-5", _msg(None, 17_000, 11_000))
    assert llm.warnings() == [] and llm.total_cost_usd() < 0.5
    for _ in range(3):
        llm.record_usage("stage2", "claude-sonnet-5", _msg(None, 17_000, 16_000))
    assert llm.total_cost_usd() > 0.5 and llm.warnings() == ["cost_over_soft_cap"]
