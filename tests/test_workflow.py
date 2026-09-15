"""Step 6 워크플로 테스트 — brief.yml / probe.yml 구조 검증(yaml.safe_load, 네트워크 없음)."""
from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
CRON_PRIMARY = "50 21 * * 0-4"
CRON_BACKUP = "35 22 * * 0-4"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    # yaml은 `on:`을 불리언 True 키로 로드한다.
    return wf.get("on", wf.get(True))


def _steps(wf: dict) -> list[dict]:
    return wf["jobs"]["brief"]["steps"]


def _step(wf: dict, name: str) -> dict:
    return next(s for s in _steps(wf) if s.get("name") == name)


def test_brief_triggers():
    on = _on(_load("brief.yml"))
    crons = [s["cron"] for s in on["schedule"]]
    assert crons == [CRON_PRIMARY, CRON_BACKUP]
    assert "workflow_dispatch" in on


def test_brief_top_level():
    wf = _load("brief.yml")
    assert wf["permissions"] == {"contents": "write"}
    assert wf["concurrency"]["group"] == "brief"
    assert wf["concurrency"]["cancel-in-progress"] is False
    assert wf["env"]["TZ"] == "Asia/Seoul"
    assert wf["jobs"]["brief"]["timeout-minutes"] == 20


def test_brief_setup_steps():
    wf = _load("brief.yml")
    steps = _steps(wf)
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    py = next(s for s in steps if str(s.get("uses", "")).startswith("actions/setup-python@"))
    assert py["with"]["cache"] == "pip"
    assert py["with"]["cache-dependency-path"] == "requirements.lock"
    runs = [s.get("run", "") for s in steps]
    assert any("pip install -r requirements.lock" in r for r in runs)
    assert any("pip install -e ." in r for r in runs)


def test_brief_run_step():
    step = _step(_load("brief.yml"), "Run brief")
    assert "python -m brief.run" in step["run"]
    assert "--run-kind" in step["run"]
    assert "github.event_name == 'schedule' && '--skip-if-done'" in step["run"]
    assert "ANTHROPIC_API_KEY" in step["env"]
    run_kind = step["env"]["RUN_KIND"]
    assert f"github.event.schedule == '{CRON_BACKUP}' && 'backup'" in run_kind
    assert "github.event_name == 'workflow_dispatch' && 'manual'" in run_kind
    assert "'primary'" in run_kind


def test_brief_always_steps():
    wf = _load("brief.yml")
    always = [s for s in _steps(wf) if s.get("if") == "always()"]
    assert len(always) == 2
    fallback = _step(wf, "Fallback if runner killed")
    assert fallback.get("if") == "always()"
    assert "brief.fallback --reason runner_killed" in fallback["run"]
    assert "docs/status/$(TZ=Asia/Seoul date +%F).json" in fallback["run"]
    publish = _step(wf, "Publish")
    assert publish.get("if") == "always()"
    for needle in (
        "rebase --abort",
        "git diff --cached --quiet",
        "TZ=Asia/Seoul date +%F",
        'git config user.name "brief-bot"',
        "git pull --rebase origin main",
        "git push origin HEAD:main",
        "$RUN_KIND",
    ):
        assert needle in publish["run"], needle
    assert "RUN_KIND" in publish["env"]


def test_probe_workflow_dispatch_only():
    on = _on(_load("probe.yml"))
    assert list(on) == ["workflow_dispatch"]
