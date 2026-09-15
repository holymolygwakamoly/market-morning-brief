"""Step 3 분석 계층 테스트 — Claude Code CLI는 FakeRunner로 모킹(subprocess 없음)."""
from __future__ import annotations

import json
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from brief.analyze.client import CallCapExceeded, CLIError, LLMClient, SkippedForDeadline
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


def _cli_json(*, structured_output=None, input_tokens=1000, output_tokens=500, cost=0.05,
              is_error=False, result="ok"):
    """`claude -p --output-format json`이 stdout에 찍는 결과 객체."""
    return {
        "type": "result", "subtype": "success", "is_error": is_error, "result": result,
        "structured_output": structured_output, "session_id": "s", "total_cost_usd": cost,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        "modelUsage": {},
    }


def _msg(parsed, inp=1000, out=500, cost=0.05):
    """성공 응답. parsed는 pydantic 모델 또는 dict → structured_output."""
    so = parsed.model_dump() if hasattr(parsed, "model_dump") else parsed
    return _cli_json(structured_output=so, input_tokens=inp, output_tokens=out, cost=cost)


def _proc(stdout, returncode=0, stderr=""):
    if isinstance(stdout, dict):
        stdout = json.dumps(stdout, ensure_ascii=False)
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRunner:
    """`claude -p` subprocess 모킹. responses 원소: dict(CLI JSON) | CompletedProcess | Exception."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.runs: list[dict] = []  # {argv, input, env, timeout}

    def __call__(self, argv, user, env, timeout):
        self.runs.append({"argv": list(argv), "input": user, "env": dict(env), "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, dict):
            r = _proc(r)
        return r

    @property
    def timeouts(self):
        return [r["timeout"] for r in self.runs]

    def opt(self, i, flag):
        argv = self.runs[i]["argv"]
        return argv[argv.index(flag) + 1]


def _llm(fake, sleeps=None):
    return LLMClient(runner=fake, claude_bin="fake-claude",
                     sleep=(sleeps.append if sleeps is not None else lambda s: None))


@pytest.fixture(autouse=True)
def _fake_which(monkeypatch):
    monkeypatch.setattr("brief.analyze.client.shutil.which", lambda name: "C:/fake/claude.cmd")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("CLAUDE_PID", "123")
    monkeypatch.setenv("KEEP_ME", "1")


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


def test_stage1_cli_argv_env_and_stdin():
    arts = [_article(i) for i in range(3)]
    fake = FakeRunner([_msg(_stage1_result(3))])
    res = run_stage1(_llm(fake), arts, deadline=FAR, model="sonnet")
    assert isinstance(res, Stage1Result) and len(res.items) == 3
    run = fake.runs[0]
    argv = run["argv"]
    assert argv[0] == "C:/fake/claude.cmd" and argv[1] == "-p"
    assert fake.opt(0, "--model") == "sonnet"
    assert "--no-session-persistence" in argv and "--bare" not in argv
    assert fake.opt(0, "--tools") == "" and fake.opt(0, "--output-format") == "json"
    assert json.loads(fake.opt(0, "--json-schema")) == Stage1Result.model_json_schema()
    assert "태깅" in fake.opt(0, "--system-prompt")
    assert "0 | 연합 | KR | ko | 기사 0" in run["input"]
    env = run["env"]
    assert "CLAUDECODE" not in env and not any(k.startswith("CLAUDE") for k in env)
    assert env["KEEP_ME"] == "1" and "PATH" in env


def test_stage1_default_model_from_config():
    fake = FakeRunner([_msg(_stage1_result(1))])
    run_stage1(_llm(fake), [_article(0)], deadline=FAR)
    assert fake.opt(0, "--model") == "haiku"


def test_missing_claude_binary_raises_clierror(monkeypatch):
    monkeypatch.setattr("brief.analyze.client.shutil.which", lambda name: None)
    fake = FakeRunner([_msg(_stage1_result(1))] * 2)
    llm = _llm(fake)
    with pytest.raises(Stage1Degraded):
        run_stage1(llm, [_article(0)], deadline=FAR)
    assert fake.runs == [] and all("CLIError" in e for e in llm.errors["stage1"])


def test_stage1_invalid_index_triggers_retry_then_success():
    bad = Stage1Result(items=[Stage1Item(i=99, p=3, m="KR", a=False, s=[], k="x")])
    fake = FakeRunner([_msg(bad), _msg(_stage1_result(2))])
    llm = _llm(fake)
    res = run_stage1(llm, [_article(i) for i in range(2)], deadline=FAR)
    assert len(res.items) == 2 and llm.calls["stage1"] == 2 and len(fake.runs) == 2
    assert "ValueError" in llm.errors["stage1"][0]


def test_stage1_always_is_error_hits_cap_of_2_then_degraded():
    fake = FakeRunner([_cli_json(is_error=True, result="Not logged in")] * 5)
    llm = _llm(fake)
    with pytest.raises(Stage1Degraded):
        run_stage1(llm, [_article(0)], deadline=FAR)
    assert llm.calls["stage1"] == 2 and len(fake.runs) == 2
    assert all("CLIError" in e and "Not logged in" in e for e in llm.errors["stage1"])


def test_stage1_nonzero_returncode_is_clierror_then_retry():
    fake = FakeRunner([_proc("", returncode=1, stderr="boom"), _msg(_stage1_result(1))])
    llm = _llm(fake)
    res = run_stage1(llm, [_article(0)], deadline=FAR)
    assert len(res.items) == 1 and llm.calls["stage1"] == 2
    assert "CLIError" in llm.errors["stage1"][0] and "boom" in llm.errors["stage1"][0]


def test_stage1_null_structured_output_is_clierror():
    fake = FakeRunner([_cli_json(structured_output=None, result="free text")] * 2)
    llm = _llm(fake)
    with pytest.raises(Stage1Degraded):
        run_stage1(llm, [_article(0)], deadline=FAR)
    assert llm.calls["stage1"] == 2
    assert "CLIError" in llm.errors["stage1"][0] and "free text" in llm.errors["stage1"][0]


def test_stage1_timeout_expired_is_failure_then_retry():
    fake = FakeRunner([subprocess.TimeoutExpired(["claude"], 180), _msg(_stage1_result(1))])
    sleeps: list[float] = []
    llm = _llm(fake, sleeps)
    res = run_stage1(llm, [_article(0)], deadline=FAR)
    assert len(res.items) == 1 and llm.calls["stage1"] == 2 and sleeps == [5]
    assert "TimeoutExpired" in llm.errors["stage1"][0]


def test_stage1_unparseable_stdout_is_clierror():
    fake = FakeRunner([_proc("garbage no json"), _proc("prefix {not json")])
    llm = _llm(fake)
    with pytest.raises(Stage1Degraded):
        run_stage1(llm, [_article(0)], deadline=FAR)
    assert all("CLIError" in e for e in llm.errors["stage1"])


def test_stage1_schema_mismatch_is_validation_error_then_retry():
    fake = FakeRunner([_cli_json(structured_output={"items": [{"i": "x"}]}), _msg(_stage1_result(1))])
    llm = _llm(fake)
    res = run_stage1(llm, [_article(0)], deadline=FAR)
    assert len(res.items) == 1 and "ValidationError" in llm.errors["stage1"][0]


def test_stdout_with_leading_noise_is_parsed():
    fake = FakeRunner([_proc("warning: something\n" + json.dumps(_msg(_stage1_result(1))))])
    res = run_stage1(_llm(fake), [_article(0)], deadline=FAR)
    assert len(res.items) == 1


# --- stage2 -----------------------------------------------------------------------


def test_stage2_cli_argv_and_stdin():
    fake = FakeRunner([_msg(_report_out())])
    report, warnings = run_stage2(_llm(fake), _selected(), [_quote()], date(2026, 9, 18), deadline=FAR)
    assert isinstance(report, Report) and warnings == []
    argv = fake.runs[0]["argv"]
    assert argv[1] == "-p" and fake.opt(0, "--model") == "sonnet"
    assert "--no-session-persistence" in argv and "--bare" not in argv
    assert fake.opt(0, "--tools") == "" and fake.opt(0, "--output-format") == "json"
    assert json.loads(fake.opt(0, "--json-schema")) == ReportOut.model_json_schema()
    assert "2026-09-18" in fake.opt(0, "--system-prompt")
    user = fake.runs[0]["input"]
    assert "^GSPC" in user and "6500.12" in user and "이전 출력" not in user
    assert not any(k.startswith("CLAUDE") for k in fake.runs[0]["env"])


def test_stage2_invalid_then_valid_retries_with_reasons():
    bad = _report_out(ai_sector="짧은 AI", headline5=VALID_REPORT["headline5"][:4])
    fake = FakeRunner([_msg(bad), _msg(_report_out())])
    llm = _llm(fake)
    report, warnings = run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=FAR)
    assert isinstance(report, Report) and warnings == []
    assert llm.calls["stage2"] == 2 and len(fake.runs) == 2
    second = fake.runs[1]["input"]
    assert "이전 출력이 다음 검증에 실패했습니다" in second
    assert "ai_sector" in second and "headline5" in second


def test_stage2_always_invalid_non_ai_rule_hits_cap_of_3():
    bad = _report_out(headline5=["하나"])
    fake = FakeRunner([_msg(bad)] * 5)
    llm = _llm(fake)
    with pytest.raises(CallCapExceeded):
        run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=FAR)
    assert llm.calls["stage2"] == 3 and len(fake.runs) == 3


def test_stage2_ai_sector_only_failure_on_last_attempt_returns_relaxed():
    short = _report_out(ai_sector="AI 섹터 조용")
    fake = FakeRunner([_msg(short)] * 3)
    llm = _llm(fake)
    report, warnings = run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=FAR)
    assert warnings == ["ai_sector_short"]
    assert isinstance(report, Report) and report.ai_sector == "AI 섹터 조용"
    assert len(report.headline5) == 5 and llm.calls["stage2"] == 3


def test_stage2_cli_error_then_valid_recovers():
    fake = FakeRunner([_proc("", returncode=2, stderr="overloaded"), _msg(_report_out())])
    sleeps: list[float] = []
    llm = _llm(fake, sleeps)
    report, _ = run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=FAR)
    assert isinstance(report, Report) and llm.calls["stage2"] == 2 and sleeps == [5]
    assert "CLIError" in llm.errors["stage2"][0]


def test_stage2_timeout_then_cap():
    fake = FakeRunner([subprocess.TimeoutExpired(["claude"], 360)] * 3)
    llm = _llm(fake)
    with pytest.raises(CallCapExceeded) as ei:
        run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=FAR)
    assert llm.calls["stage2"] == 3 and isinstance(ei.value.last_error, subprocess.TimeoutExpired)


# --- deadline ---------------------------------------------------------------------


def test_deadline_too_close_skips_without_calls():
    fake = FakeRunner([_msg(_report_out())])
    llm = _llm(fake)
    with pytest.raises(SkippedForDeadline):
        run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=time.monotonic() + 10)
    assert llm.calls.get("stage2", 0) == 0 and fake.runs == []
    with pytest.raises(Stage1Degraded):
        run_stage1(llm, [_article(0)], deadline=time.monotonic() + 10)
    assert llm.calls.get("stage1", 0) == 0 and fake.runs == []


def test_call_timeout_derived_from_remaining():
    fake = FakeRunner([_msg(_stage1_result(1))])
    run_stage1(_llm(fake), [_article(0)], deadline=time.monotonic() + 100)
    assert 35 <= fake.timeouts[0] <= 40  # min(budget 180, remaining-60)


def test_call_timeout_capped_by_stage_budget():
    fake = FakeRunner([_msg(_stage1_result(1))])
    run_stage1(_llm(fake), [_article(0)], deadline=time.monotonic() + 800)
    assert 295 <= fake.timeouts[0] <= 300


def test_stage2_timeout_capped_by_stage_budget_480():
    fake = FakeRunner([_msg(_report_out())])
    run_stage2(_llm(fake), _selected(), [], date(2026, 9, 18), deadline=time.monotonic() + 1000)
    assert 475 <= fake.timeouts[0] <= 480


def test_stage2_timeout_derived_from_remaining():
    fake = FakeRunner([_msg(_report_out())])
    run_stage2(_llm(fake), _selected(), [], date(2026, 9, 18), deadline=time.monotonic() + 300)
    assert 235 <= fake.timeouts[0] <= 240


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


def test_usage_from_cli_json_and_summary():
    llm = _llm(FakeRunner([]))
    llm.record_usage("stage1", "sonnet", _cli_json(input_tokens=32_000, output_tokens=6_000, cost=0.124))
    u = llm.usages[-1]
    assert (u.input_tokens, u.output_tokens, u.cost_usd, u.billed) == (32_000, 6_000, 0.124, False)
    llm.record_usage("stage1", "sonnet", _cli_json(input_tokens=32_000, output_tokens=6_000, cost=0.062))
    llm.record_usage("stage2", "sonnet", _cli_json(input_tokens=1_000_000, output_tokens=0, cost=2.0))
    llm.record_usage("stage2", "sonnet", {"usage": {}})  # 필드 누락 → 0
    s = llm.summary()
    assert s["engine"] == "claude-cli" and s["model"] == "sonnet" and s["billed"] is False
    assert s["total_cost_usd"] == pytest.approx(2.186)
    assert s["stages"]["stage1"]["input_tokens"] == 64_000
    assert s["stages"]["stage1"]["cost_estimate_usd"] == pytest.approx(0.186)
    assert s["total_input_tokens"] == 1_064_000 and s["total_output_tokens"] == 12_000
    assert s["warnings"] == ["cost_over_soft_cap"]


def test_usage_recorded_per_cli_run_through_stages():
    fake = FakeRunner([_msg(_stage1_result(1), 3000, 400, cost=0.01),
                       _msg(_report_out(), 17_000, 11_000, cost=0.14)])
    llm = _llm(fake)
    run_stage1(llm, [_article(0)], deadline=FAR)
    run_stage2(llm, _selected(), [], date(2026, 9, 18), deadline=FAR)
    s = llm.summary()
    assert s["stages"]["stage1"] == {"model": "haiku", "input_tokens": 3000, "output_tokens": 400,
                                     "cost_estimate_usd": 0.01, "calls": 1}
    assert s["stages"]["stage2"]["calls"] == 1 and s["total_calls"] == 2
    assert s["total_cost_usd"] == pytest.approx(0.15) and s["warnings"] == []


def test_cost_soft_cap_warning():
    llm = _llm(FakeRunner([]))
    llm.record_usage("stage2", "sonnet", _cli_json(cost=0.14))
    assert llm.warnings() == [] and llm.total_cost_usd() < 0.5
    for _ in range(3):
        llm.record_usage("stage2", "sonnet", _cli_json(cost=0.19))
    assert llm.total_cost_usd() > 0.5 and llm.warnings() == ["cost_over_soft_cap"]


def test_default_runner_strips_claude_env_and_uses_stdin(monkeypatch):
    """실제 subprocess.run 호출 인자 확인(프로세스는 띄우지 않음)."""
    from brief.analyze import client as c

    captured = {}

    def fake_run(argv, **kw):
        captured.update(argv=argv, **kw)
        return _proc(_msg(_stage1_result(1)))

    monkeypatch.setattr(c.subprocess, "run", fake_run)
    llm = LLMClient(claude_bin="claude")
    out = llm.structured_once("stage1", system="s", user="u", output_format=Stage1Result,
                              model="sonnet", timeout=42)
    assert isinstance(out, Stage1Result)
    assert captured["input"] == "u" and captured["timeout"] == 42
    assert captured["text"] is True and captured["encoding"] == "utf-8" and captured["capture_output"] is True
    assert "shell" not in captured
    assert not any(k.startswith("CLAUDE") for k in captured["env"]) and captured["env"]["KEEP_ME"] == "1"
    assert captured["argv"][0] == "C:/fake/claude.cmd"


def test_is_error_message_includes_result_and_stderr():
    fake = FakeRunner([_proc(_cli_json(is_error=True, result="Not logged in"), stderr="auth failed")])
    llm = _llm(fake)
    with pytest.raises(CLIError, match="Not logged in.*auth failed"):
        llm.structured_once("stage1", system="s", user="u", output_format=Stage1Result,
                            model="sonnet", timeout=10)
