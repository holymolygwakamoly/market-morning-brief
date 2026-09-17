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
from brief.analyze.schemas import SectorReportOut, Stage1Item, Stage1Result, Stage1Tags, TopicReportOut
from brief.analyze.select import select_for_topics
from brief.analyze.stage1_tag import Stage1Degraded, run_stage1
from brief.analyze.topics import DISCLAIMER, run_topic, run_topics
from brief.collect.base import Article
from brief.market.base import MarketSnapshot

FIXTURES = Path(__file__).parent / "fixtures"
VALID_TOPIC = json.loads((FIXTURES / "topic_valid.json").read_text(encoding="utf-8"))
VALID_SECTOR = json.loads((FIXTURES / "sector_valid.json").read_text(encoding="utf-8"))
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


def _topic_out(**overrides) -> TopicReportOut:
    return TopicReportOut.model_validate({**VALID_TOPIC, **overrides})


def _sector_out(**overrides) -> SectorReportOut:
    return SectorReportOut.model_validate({**VALID_SECTOR, **overrides})


def _selected(topic="us"):
    arts = [_article(i, category=c) for i, c in enumerate(["US", "KR", "MACRO", "EU", "CN", "GLOBAL"])]
    return select_for_topics(arts, None)[topic]


def _market() -> MarketSnapshot:
    return MarketSnapshot(run_date=date(2026, 9, 18))


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


@pytest.mark.parametrize("model", [Stage1Result, TopicReportOut, SectorReportOut])
def test_transport_schema_has_no_length_constraints_and_forbids_extra(model):
    schema = model.model_json_schema()
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    for d in schema.get("$defs", {}).values():
        if d.get("type") == "object":
            assert d["additionalProperties"] is False
    _walk(schema)


# --- stage1 -----------------------------------------------------------------------


def _stage1_result(n):
    return Stage1Result(items=[Stage1Item(i=i, p=3, m="KR", a=False, s=["OTHER"], k=f"s{i}") for i in range(n)])


def test_stage1_cli_argv_env_and_stdin():
    arts = [_article(i) for i in range(3)]
    fake = FakeRunner([_msg(_stage1_result(3))])
    res = run_stage1(_llm(fake), arts, deadline=FAR, model="sonnet")
    assert isinstance(res, Stage1Tags) and len(res.items) == 3
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
    assert fake.opt(0, "--model") == "opus"


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


# --- 토픽 보고서 validator ---------------------------------------------------------------


def test_topic_valid_fixtures_pass():
    from brief.analyze.schemas import sector_report_violations, topic_report_violations

    assert topic_report_violations(_topic_out(), DISCLAIMER) == []
    assert sector_report_violations(_sector_out(), DISCLAIMER) == []


def test_topic_length_rules_are_prefixed_and_other_rules_not():
    from brief.analyze.schemas import LENGTH_PREFIX, topic_report_violations

    short = _topic_out(overview="짧다", sections=VALID_TOPIC["sections"][:2], headlines=[], disclaimer="다른 문구")
    errs = topic_report_violations(short, DISCLAIMER)
    assert any(e.startswith(LENGTH_PREFIX) and "overview" in e for e in errs)
    assert any(e.startswith(LENGTH_PREFIX) and "sections" in e for e in errs)
    assert any("headlines" in e and not e.startswith(LENGTH_PREFIX) for e in errs)
    assert any("disclaimer" in e for e in errs)


def test_sector_rules():
    from brief.analyze.schemas import sector_report_violations

    bad = _sector_out(ai_sector="짧은 AI")
    errs = sector_report_violations(bad, DISCLAIMER)
    assert len(errs) == 1 and "ai_sector" in errs[0] and errs[0].startswith("[length]")
    sectors = json.loads(json.dumps(VALID_SECTOR["sectors"]))
    sectors[0]["leaders"] = sectors[0]["leaders"] * 6
    errs = sector_report_violations(_sector_out(sectors=sectors), DISCLAIMER)
    assert any("leaders" in e and not e.startswith("[length]") for e in errs)


# --- stage2 (토픽) --------------------------------------------------------------------


def test_topic_cli_argv_and_stdin_include_market_and_articles():
    fake = FakeRunner([_msg(_topic_out())])
    res = run_topic(_llm(fake), "us", _selected("us"), _market(), date(2026, 9, 18), deadline=FAR)
    assert res.ok and res.warnings == [] and res.attempts == 1
    argv = fake.runs[0]["argv"]
    assert argv[1] == "-p" and fake.opt(0, "--model") == "opus"
    assert "--no-session-persistence" in argv and "--bare" not in argv
    assert fake.opt(0, "--tools") == "" and fake.opt(0, "--output-format") == "json"
    assert json.loads(fake.opt(0, "--json-schema")) == TopicReportOut.model_json_schema()
    sysp = fake.opt(0, "--system-prompt")
    assert "2026-09-18" in sysp and "미국 주식" in sysp and DISCLAIMER in sysp
    user = fake.runs[0]["input"]
    assert "# 시장 데이터" in user and "# 선별 기사" in user and "기사 0" in user and "이전 출력" not in user
    assert not any(k.startswith("CLAUDE") for k in fake.runs[0]["env"])


def test_sectors_topic_uses_sector_schema_and_thebell():
    fake = FakeRunner([_msg(_sector_out())])
    arts = [_article(i, category="US") for i in range(3)] + [_article(9, source="더벨", title="더벨 딜")]
    sel = select_for_topics(arts, None)["sectors"]
    res = run_topic(_llm(fake), "sectors", sel, _market(), date(2026, 9, 18), deadline=FAR)
    assert res.ok and isinstance(res.report, SectorReportOut)
    assert json.loads(fake.opt(0, "--json-schema")) == SectorReportOut.model_json_schema()
    assert "더벨 헤드라인" in fake.runs[0]["input"] and "더벨 딜" in fake.runs[0]["input"]


def test_topic_invalid_then_valid_retries_with_reasons():
    bad = _topic_out(headlines=[], disclaimer="x")
    fake = FakeRunner([_msg(bad), _msg(_topic_out())])
    llm = _llm(fake)
    res = run_topic(llm, "kr", _selected("kr"), _market(), date(2026, 9, 18), deadline=FAR)
    assert res.ok and res.attempts == 2 and llm.calls["stage2:kr"] == 2
    second = fake.runs[1]["input"]
    assert "이전 출력이 다음 검증에 실패했습니다" in second and "headlines" in second and "disclaimer" in second


def test_topic_always_invalid_non_length_rule_hits_cap_of_2():
    bad = _topic_out(disclaimer="x")
    fake = FakeRunner([_msg(bad)] * 5)
    llm = _llm(fake)
    res = run_topic(llm, "macro", _selected("macro"), _market(), date(2026, 9, 18), deadline=FAR)
    assert not res.ok and "CallCapExceeded" in res.error and llm.calls["stage2:macro"] == 2 and len(fake.runs) == 2


def test_topic_length_only_failure_on_last_attempt_returns_relaxed():
    short = _topic_out(overview="짧은 개요")
    fake = FakeRunner([_msg(short)] * 3)
    llm = _llm(fake)
    res = run_topic(llm, "eu", _selected("eu"), _market(), date(2026, 9, 18), deadline=FAR)
    assert res.ok and res.warnings == ["length_short"] and res.report.overview == "짧은 개요" and llm.calls["stage2:eu"] == 2


def test_topic_cli_error_then_valid_recovers():
    fake = FakeRunner([_proc("", returncode=2, stderr="overloaded"), _msg(_topic_out())])
    sleeps: list[float] = []
    llm = _llm(fake, sleeps)
    res = run_topic(llm, "cn", _selected("cn"), _market(), date(2026, 9, 18), deadline=FAR)
    assert res.ok and llm.calls["stage2:cn"] == 2 and sleeps == [5]
    assert "CLIError" in llm.errors["stage2:cn"][0]


def test_topic_timeout_then_cap():
    fake = FakeRunner([subprocess.TimeoutExpired(["claude"], 480)] * 3)
    llm = _llm(fake)
    res = run_topic(llm, "us", _selected("us"), _market(), date(2026, 9, 18), deadline=FAR)
    assert not res.ok and "CallCapExceeded" in res.error and llm.calls["stage2:us"] == 2


def test_run_topics_parallel_isolates_failures():
    responses = {"TopicReportOut": _msg(_topic_out()), "SectorReportOut": _proc("", returncode=1, stderr="boom")}

    class Router(FakeRunner):
        def __call__(self, argv, user, env, timeout):
            self.runs.append({"argv": list(argv), "input": user, "env": dict(env), "timeout": timeout})
            title = json.loads(argv[argv.index("--json-schema") + 1])["title"]
            r = responses[title]
            return _proc(r) if isinstance(r, dict) else r

    fake = Router([])
    arts = [_article(i, category=c) for i, c in enumerate(["US", "KR", "MACRO", "EU", "CN", "GLOBAL"])]
    sel = select_for_topics(arts, None)
    res = run_topics(_llm(fake), sel, _market(), date(2026, 9, 18), deadline=FAR, max_parallel=3)
    assert set(res) == set(sel) and all(res[t].ok for t in ("macro", "us", "eu", "kr", "cn")) and not res["sectors"].ok
    assert len(fake.runs) == 5 + 2


# --- deadline ---------------------------------------------------------------------


def test_deadline_too_close_skips_without_calls():
    fake = FakeRunner([_msg(_topic_out())])
    llm = _llm(fake)
    res = run_topic(llm, "us", _selected("us"), _market(), date(2026, 9, 18), deadline=time.monotonic() + 10)
    assert not res.ok and "SkippedForDeadline" in res.error
    assert llm.calls.get("stage2:us", 0) == 0 and fake.runs == []
    with pytest.raises(Stage1Degraded):
        run_stage1(llm, [_article(0)], deadline=time.monotonic() + 10)
    assert llm.calls.get("stage1", 0) == 0 and fake.runs == []


def test_call_timeout_derived_from_remaining():
    fake = FakeRunner([_msg(_stage1_result(1))])
    run_stage1(_llm(fake), [_article(0)], deadline=time.monotonic() + 100)
    assert 35 <= fake.timeouts[0] <= 40  # min(budget, remaining-60)


def test_call_timeout_capped_by_stage_budget():
    fake = FakeRunner([_msg(_stage1_result(1))])
    run_stage1(_llm(fake), [_article(0)], deadline=time.monotonic() + 800)
    assert 475 <= fake.timeouts[0] <= 480


def test_topic_timeout_capped_by_budget_480_and_remaining():
    fake = FakeRunner([_msg(_topic_out()), _msg(_topic_out())])
    run_topic(_llm(fake), "us", _selected("us"), _market(), date(2026, 9, 18), deadline=time.monotonic() + 1000)
    run_topic(_llm(fake), "us", _selected("us"), _market(), date(2026, 9, 18), deadline=time.monotonic() + 300)
    assert 475 <= fake.timeouts[0] <= 480 and 235 <= fake.timeouts[1] <= 240


# --- usage / cost ---------------------------------------------------------------------


def test_usage_from_cli_json_and_summary():
    llm = _llm(FakeRunner([]))
    llm.record_usage("stage1", "sonnet", _cli_json(input_tokens=32_000, output_tokens=6_000, cost=0.124))
    u = llm.usages[-1]
    assert (u.input_tokens, u.output_tokens, u.cost_usd, u.billed) == (32_000, 6_000, 0.124, False)
    llm.record_usage("stage1", "sonnet", _cli_json(input_tokens=32_000, output_tokens=6_000, cost=0.062))
    llm.record_usage("stage2:us", "sonnet", _cli_json(input_tokens=1_000_000, output_tokens=0, cost=2.0))
    llm.record_usage("stage2:us", "sonnet", {"usage": {}})  # 필드 누락 → 0
    s = llm.summary()
    assert s["engine"] == "claude-cli" and s["model"] == "sonnet" and s["billed"] is False
    assert s["total_cost_usd"] == pytest.approx(2.186)
    assert s["stages"]["stage1"]["input_tokens"] == 64_000
    assert s["stages"]["stage1"]["cost_estimate_usd"] == pytest.approx(0.186)
    assert s["total_input_tokens"] == 1_064_000 and s["total_output_tokens"] == 12_000
    assert s["warnings"] == []  # 구독 실행: 비용 경고 없음


def test_usage_recorded_per_cli_run_through_stages():
    fake = FakeRunner([_msg(_stage1_result(1), 3000, 400, cost=0.01), _msg(_topic_out(), 17_000, 11_000, cost=0.14)])
    llm = _llm(fake)
    run_stage1(llm, [_article(0)], deadline=FAR)
    run_topic(llm, "us", _selected("us"), _market(), date(2026, 9, 18), deadline=FAR)
    s = llm.summary()
    assert s["stages"]["stage1"] == {"model": "opus", "input_tokens": 3000, "output_tokens": 400, "cost_estimate_usd": 0.01, "calls": 1}
    assert s["stages"]["stage2:us"]["calls"] == 1 and s["total_calls"] == 2
    assert s["total_cost_usd"] == pytest.approx(0.15)


def test_default_runner_strips_claude_env_and_uses_stdin(monkeypatch):
    """실제 subprocess.run 호출 인자 확인(프로세스는 띄우지 않음)."""
    from brief.analyze import client as c

    captured = {}

    def fake_run(argv, **kw):
        captured.update(argv=argv, **kw)
        return _proc(_msg(_stage1_result(1)))

    monkeypatch.setattr(c.subprocess, "run", fake_run)
    llm = LLMClient(claude_bin="claude")
    out = llm.structured_once("stage1", system="s", user="u", output_format=Stage1Result, model="sonnet", timeout=42)
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
        llm.structured_once("stage1", system="s", user="u", output_format=Stage1Result, model="sonnet", timeout=10)
