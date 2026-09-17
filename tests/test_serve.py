"""로컬 대시보드 서버 테스트(v4 §11.6) — 실제 생성 없음, 페이크 update_fn/regen_fn/git_runner 주입.

규칙: 업데이트는 오늘(KST)만·하루 1회(409), 재생성은 inputs.json 있을 때만, 정적 파일은 docs/ 루트에서 서빙.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from brief.config import KST
from brief.serve import make_server, publish, validate_date

TODAY = datetime.now(KST).date()


def _iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _write_snapshot(docs: Path, date: str, result: str = "success", error: str | None = None, with_inputs: bool = True) -> None:
    base = docs / "data" / date
    base.mkdir(parents=True, exist_ok=True)
    (base / "status.json").write_text(json.dumps({"date": date, "result": result, "error": error, "warnings": [], "topics": {"us": {"ok": True}}, "finished_at_kst": f"{date}T09:00:00+09:00"}), encoding="utf-8")
    (base / "home.json").write_text(json.dumps({"date": date, "result": result, "indices": [], "topics": {}}), encoding="utf-8")
    if with_inputs:
        (base / "inputs.json").write_text(json.dumps({"articles": [], "stage1": None}), encoding="utf-8")


class Client:
    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"

    def request(self, path: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, dict, bytes]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def json(self, path: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
        code, _, raw = self.request(path, method=method, body=body)
        return code, json.loads(raw.decode("utf-8"))


@pytest.fixture
def server_factory(tmp_path):
    servers = []

    def factory(update_fn=None, regen_fn=None, git_runner=subprocess.run, docs: Path | None = None):
        docs = docs or (tmp_path / "docs")
        srv = make_server(docs, 0, update_fn=update_fn, regen_fn=regen_fn, git_runner=git_runner)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append(srv)
        return srv, Client(srv.server_address[1]), docs

    yield factory
    for s in servers:
        s.shutdown()
        s.server_close()


def _wait_done(client: Client, timeout: float = 5.0) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        _, st = client.json("/api/status")
        if not st["running"]:
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# --- 날짜 검증 -------------------------------------------------------------------


@pytest.mark.parametrize("s, ok", [(_iso(0), True), (_iso(-1), False), (_iso(1), False), ("2026-9-1", False), ("2026-02-30", False), (None, False)])
def test_validate_date_today_only(s, ok):
    v = validate_date(s)
    assert (not isinstance(v, str)) is ok


# --- 업데이트 -----------------------------------------------------------------------


def test_update_rejects_past_and_future_dates(server_factory):
    _, c, _ = server_factory(update_fn=lambda date, docs: None)
    for d in (_iso(-1), _iso(-3), _iso(1)):
        code, body = c.json("/api/update", method="POST", body={"date": d})
        assert code == 400 and "오늘" in body["error"], d


def test_update_runs_today_and_status_reflects_result(server_factory):
    seen = []

    def fake_update(date, docs):
        seen.append((date, Path(docs)))
        _write_snapshot(Path(docs), date, result="degraded")

    _, c, docs = server_factory(update_fn=fake_update)
    code, body = c.json("/api/update", method="POST", body={"date": _iso(0)})
    assert code == 202 and body["ok"]
    st = _wait_done(c)
    assert st["kind"] == "update" and st["date"] == _iso(0) and st["result"] == "degraded" and st["stage"] == "done"
    assert seen[0][0] == _iso(0) and seen[0][1] == docs.resolve()
    assert "today" in st and st["today"] == _iso(0) and len(st["topics"]) == 6
    # 하루 1회: 이미 완료된 오늘은 409
    code, body = c.json("/api/update", method="POST", body={"date": _iso(0)})
    assert code == 409 and "이미 완료" in body["error"]


def test_failed_update_can_be_retried(server_factory):
    calls = []

    def fake_update(date, docs):
        calls.append(1)
        _write_snapshot(Path(docs), date, result="failed", error="boom")

    _, c, _ = server_factory(update_fn=fake_update)
    c.json("/api/update", method="POST", body={"date": _iso(0)})
    st = _wait_done(c)
    assert st["result"] == "failed" and st["error"] == "boom"
    code, _ = c.json("/api/update", method="POST", body={"date": _iso(0)})
    assert code == 202
    _wait_done(c)
    assert len(calls) == 2


def test_crashing_update_fn_does_not_wedge_server(server_factory):
    def crash(date, docs):
        raise RuntimeError("kaboom")

    _, c, _ = server_factory(update_fn=crash)
    c.json("/api/update", method="POST", body={"date": _iso(0)})
    st = _wait_done(c)
    assert st["result"] == "failed" and "kaboom" in st["error"]
    code, _ = c.json("/api/update", method="POST", body={"date": _iso(0)})
    assert code == 202


def test_409_while_running(server_factory):
    gate = threading.Event()

    def slow(date, docs):
        gate.wait(5)
        _write_snapshot(Path(docs), date)

    _, c, _ = server_factory(update_fn=slow)
    code, _ = c.json("/api/update", method="POST", body={"date": _iso(0)})
    assert code == 202
    code, body = c.json("/api/update", method="POST", body={"date": _iso(0)})
    assert code == 409 and "작업 중" in body["error"]
    code, body = c.json("/api/regenerate", method="POST", body={"date": _iso(0), "topic": "us"})
    assert code == 409
    gate.set()
    _wait_done(c)


def test_bad_json_body(server_factory):
    _, c, _ = server_factory()
    code, _, raw = c.request("/api/update", method="POST", body=None)
    assert code == 400
    req = urllib.request.Request(c.base + "/api/update", data=b"{not json", method="POST")
    req.add_header("Content-Type", "application/json")
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 400


# --- 재생성 ---------------------------------------------------------------------------


def test_regenerate_requires_inputs_and_valid_topic(server_factory):
    seen = []

    def fake_regen(date, topics, docs):
        seen.append((date, topics))
        _write_snapshot(Path(docs), date, result="success")

    _, c, docs = server_factory(regen_fn=fake_regen)
    code, body = c.json("/api/regenerate", method="POST", body={"date": _iso(0), "topic": "us"})
    assert code == 409 and "먼저 [업데이트]" in body["error"]
    _write_snapshot(docs, _iso(0), result="degraded")
    code, body = c.json("/api/regenerate", method="POST", body={"date": _iso(0), "topic": "nope"})
    assert code == 400 and "topic" in body["error"]
    code, body = c.json("/api/regenerate", method="POST", body={"date": _iso(-1), "topic": "us"})
    assert code == 400
    code, body = c.json("/api/regenerate", method="POST", body={"date": _iso(0), "topic": "kr"})
    assert code == 202 and body["topic"] == "kr"
    st = _wait_done(c)
    assert st["kind"] == "regenerate" and st["topics"] == ["kr"] and st["result"] == "success"
    assert seen == [(_iso(0), ("kr",))]


# --- 정적 파일·SPA --------------------------------------------------------------------


def test_spa_and_static_files_and_traversal_guard(server_factory, tmp_path):
    _, c, docs = server_factory()
    code, headers, raw = c.request("/")
    assert code == 200 and "text/html" in headers["Content-Type"] and "리서치 대시보드" in raw.decode("utf-8")
    assert (docs / "index.html").exists() and (docs / "data" / "index.json").exists()
    _write_snapshot(docs, _iso(0))
    code, body = c.json(f"/data/{_iso(0)}/home.json")
    assert code == 200 and body["date"] == _iso(0)
    code, body = c.json(f"/docs/data/{_iso(0)}/home.json")  # 구 링크 호환
    assert code == 200
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    code, _, _ = c.request("/../secret.txt")
    assert code in (403, 404)
    code, _, _ = c.request("/%2e%2e/secret.txt")
    assert code in (403, 404)
    code, _, _ = c.request("/nope.json")
    assert code == 404


def test_status_has_can_publish_flag(server_factory):
    def no_remote(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="error: No such remote")

    _, c, _ = server_factory(git_runner=no_remote)
    _, st = c.json("/api/status")
    assert st["can_publish"] is False and st["running"] is False


# --- 게시 ---------------------------------------------------------------------


def _git_script(rules):
    """rules: (subcommand 접두 튜플 → (rc, out)) 목록. 호출 순서를 calls에 기록한다."""
    calls: list[list[str]] = []

    def runner(cmd, **kw):
        calls.append(cmd)
        sub = tuple(cmd[1:])
        for prefix, (rc, out) in rules:
            if sub[: len(prefix)] == tuple(prefix):
                rc_v = rc(calls) if callable(rc) else rc
                return subprocess.CompletedProcess(cmd, rc_v, stdout=out, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    runner.calls = calls
    return runner


def test_publish_no_changes(tmp_path):
    runner = _git_script([(("diff", "--cached", "--quiet"), (0, ""))])
    r = publish(tmp_path / "docs", git_runner=runner)
    assert r["ok"] is True and r["output"] == "no changes"
    assert [c[1] for c in runner.calls] == ["add", "diff"]
    assert runner.calls[0] == ["git", "add", "docs"]


def test_publish_success(tmp_path):
    runner = _git_script([(("diff", "--cached", "--quiet"), (1, "")), (("push",), (0, "pushed"))])
    now = datetime(2026, 9, 15, 7, 0, tzinfo=KST)
    r = publish(tmp_path / "docs", git_runner=runner, now_kst=now)
    assert r["ok"] is True and "pushed" in r["output"]
    subs = [c[1] for c in runner.calls]
    assert subs == ["add", "diff", "commit", "pull", "push"]
    assert runner.calls[2] == ["git", "commit", "-m", "dashboard: publish 2026-09-15"]
    assert runner.calls[3] == ["git", "pull", "--rebase", "origin", "main"]
    assert runner.calls[4] == ["git", "push", "origin", "HEAD:main"]


def test_publish_push_fails_three_times(tmp_path):
    runner = _git_script([(("diff", "--cached", "--quiet"), (1, "")), (("push",), (1, "rejected: non-fast-forward"))])
    r = publish(tmp_path / "docs", git_runner=runner)
    assert r["ok"] is False and "rejected" in r["output"]
    subs = [c[1] for c in runner.calls]
    assert subs.count("push") == 3 and subs.count("pull") == 3 and subs.count("rebase") == 2
    assert subs.index("rebase") > subs.index("push")


def test_publish_retry_then_success(tmp_path):
    runner = _git_script([
        (("diff", "--cached", "--quiet"), (1, "")),
        (("push",), (lambda calls: 0 if sum(1 for c in calls if c[1] == "push") >= 2 else 1, "")),
    ])
    r = publish(tmp_path / "docs", git_runner=runner)
    assert r["ok"] is True
    assert [c[1] for c in runner.calls].count("push") == 2


def test_publish_endpoint(server_factory):
    runner = _git_script([(("diff", "--cached", "--quiet"), (0, ""))])
    _, c, _ = server_factory(git_runner=runner)
    code, body = c.json("/api/publish", method="POST")
    assert code == 200 and body == {"ok": True, "output": "no changes"}
