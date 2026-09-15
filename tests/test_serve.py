"""로컬 대시보드 서버 테스트(US-010/011, AC-20) — 실제 생성 없음, 페이크 run_fn/git_runner 주입."""
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
from brief.serve import allowed_dates, make_server, publish, validate_date

TODAY = datetime.now(KST).date()


def _iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _write_result(docs: Path, date: str, result: str = "success", error: str | None = None) -> None:
    (docs / "reports").mkdir(parents=True, exist_ok=True)
    (docs / "status").mkdir(parents=True, exist_ok=True)
    (docs / "reports" / f"{date}.html").write_text(
        f'<!doctype html><html lang="ko" data-generated="{date}"><body>report {date}</body></html>', encoding="utf-8"
    )
    (docs / "status" / f"{date}.json").write_text(
        json.dumps({"date": date, "result": result, "error": error, "warnings": []}), encoding="utf-8"
    )


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

    def factory(run_fn=None, git_runner=subprocess.run, docs: Path | None = None):
        docs = docs or (tmp_path / "docs")
        srv = make_server(docs, 0, run_fn=run_fn, git_runner=git_runner)
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
    raise AssertionError("generation did not finish")


# --- 날짜 검증 ------------------------------------------------------------------


def test_allowed_dates_window():
    now = datetime(2026, 9, 15, 7, 0, tzinfo=KST)
    lo, hi = allowed_dates(now)
    assert (lo.isoformat(), hi.isoformat()) == ("2026-09-12", "2026-09-15")


@pytest.mark.parametrize(
    "s, ok",
    [
        ("2026-09-12", True), ("2026-09-15", True), ("2026-09-13", True),
        ("2026-09-11", False), ("2026-09-16", False), ("2026/09/15", False), ("", False), (None, False),
        ("2026-02-30", False),
    ],
)
def test_validate_date(s, ok):
    now = datetime(2026, 9, 15, 7, 0, tzinfo=KST)
    v = validate_date(s, now)
    assert (not isinstance(v, str)) is ok


def test_generate_date_range_over_http(server_factory):
    def fake_run(args):
        _write_result(Path(args.docs), args.date)

    _, c, _ = server_factory(run_fn=fake_run)
    for bad in (_iso(-4), _iso(1), "2026-9-1", "abc"):
        code, body = c.json("/api/generate", method="POST", body={"date": bad})
        assert code == 400 and "error" in body, bad
    code, body = c.json("/api/generate", method="POST", body={"date": _iso(-3)})
    assert code == 202 and body == {"ok": True, "date": _iso(-3)}
    _wait_done(c)
    code, body = c.json("/api/generate", method="POST", body={"date": _iso(0)})
    assert code == 202 and body["date"] == _iso(0)
    _wait_done(c)


def test_generate_bad_json_body(server_factory):
    _, c, _ = server_factory(run_fn=lambda a: None)
    code, _, raw = c.request("/api/generate", method="POST")
    assert code == 400


# --- 상태·동시성 ----------------------------------------------------------------


def test_status_keys_and_report_url_after_completion(server_factory):
    def fake_run(args):
        import logging

        logging.getLogger("brief.run").info("stage: collect")
        logging.getLogger("brief.run").info("stage: render")
        _write_result(Path(args.docs), args.date, result="degraded")

    _, c, _ = server_factory(run_fn=fake_run)
    code, st = c.json("/api/status")
    assert code == 200
    for k in ("running", "date", "stage", "log", "result", "warnings", "report_url",
              "started_at", "finished_at", "elapsed_s", "error", "recent"):
        assert k in st, k
    assert st["running"] is False and st["recent"] == []

    date = _iso(0)
    assert c.json("/api/generate", method="POST", body={"date": date})[0] == 202
    st = _wait_done(c)
    assert st["date"] == date
    assert st["result"] == "degraded"
    assert st["report_url"] == f"/docs/reports/{date}.html"
    assert st["stage"] == "done" and st["error"] is None
    assert any("stage: render" in line for line in st["log"])
    assert st["recent"][0]["date"] == date and st["recent"][0]["result"] == "degraded"
    assert st["started_at"] and st["finished_at"]


def test_failed_run_surfaces_error(server_factory):
    def fake_run(args):
        docs = Path(args.docs)
        (docs / "status").mkdir(parents=True, exist_ok=True)
        (docs / "status" / f"{args.date}.json").write_text(
            json.dumps({"date": args.date, "result": "failed", "error": "RuntimeError: kaboom"}), encoding="utf-8"
        )

    _, c, _ = server_factory(run_fn=fake_run)
    assert c.json("/api/generate", method="POST", body={"date": _iso(0)})[0] == 202
    st = _wait_done(c)
    assert st["result"] == "failed" and st["stage"] == "failed"
    assert "kaboom" in st["error"] and st["report_url"] is None


def test_crashing_run_fn_does_not_wedge_server(server_factory):
    def fake_run(args):
        raise ValueError("boom")

    _, c, _ = server_factory(run_fn=fake_run)
    assert c.json("/api/generate", method="POST", body={"date": _iso(0)})[0] == 202
    st = _wait_done(c)
    assert st["result"] == "failed" and "ValueError: boom" in st["error"]
    # 다시 생성 가능
    assert c.json("/api/generate", method="POST", body={"date": _iso(0)})[0] == 202
    _wait_done(c)


def test_409_while_running(server_factory):
    release = threading.Event()
    started = threading.Event()

    def blocking_run(args):
        started.set()
        release.wait(5)
        _write_result(Path(args.docs), args.date)

    _, c, _ = server_factory(run_fn=blocking_run)
    assert c.json("/api/generate", method="POST", body={"date": _iso(0)})[0] == 202
    assert started.wait(2)
    code, body = c.json("/api/generate", method="POST", body={"date": _iso(-1)})
    assert code == 409 and "error" in body
    _, st = c.json("/api/status")
    assert st["running"] is True and st["date"] == _iso(0)
    release.set()
    st = _wait_done(c)
    assert st["result"] == "success"


# --- 정적·대시보드 --------------------------------------------------------------


def test_static_docs_and_traversal_guard(server_factory, tmp_path):
    _, c, docs = server_factory()
    (tmp_path / "pyproject.toml").write_text("[secret]", encoding="utf-8")
    date = _iso(0)
    _write_result(docs, date)
    code, headers, raw = c.request(f"/docs/reports/{date}.html")
    assert code == 200 and headers["Content-Type"].startswith("text/html")
    assert f"report {date}" in raw.decode("utf-8")
    code, headers, _ = c.request(f"/docs/status/{date}.json")
    assert code == 200 and headers["Content-Type"].startswith("application/json")
    assert c.request("/docs/nope.html")[0] == 404
    assert c.request("/docs/reports")[0] == 404  # 디렉터리
    for p in ("/docs/../pyproject.toml", "/docs/%2e%2e/pyproject.toml", "/docs/..%2fpyproject.toml"):
        code, _, raw = c.request(p)
        assert code in (403, 404), p
        assert b"[secret]" not in raw
    assert c.request("/nothing")[0] == 404


def test_dashboard_page(server_factory):
    def fake_git(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="https://github.com/x/y.git\n", stderr="")

    _, c, docs = server_factory(git_runner=fake_git)
    _write_result(docs, _iso(-1))
    code, headers, raw = c.request("/")
    html = raw.decode("utf-8")
    assert code == 200 and headers["Content-Type"].startswith("text/html")
    assert '<html lang="ko">' in html
    assert f'min="{_iso(-3)}"' in html and f'max="{_iso(0)}"' in html and f'value="{_iso(0)}"' in html
    assert f"/docs/reports/{_iso(-1)}.html" in html
    assert "GitHub에 게시" in html and 'id="publish"' in html and 'id="publish" disabled' not in html
    assert "http://" not in html.split("<body>")[0]  # 외부 리소스 없음


def test_dashboard_publish_disabled_without_remote(server_factory):
    def fake_git(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="error: No such remote 'origin'")

    _, c, _ = server_factory(git_runner=fake_git)
    _, _, raw = c.request("/")
    assert 'id="publish" disabled' in raw.decode("utf-8")


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
    assert runner.calls[2] == ["git", "commit", "-m", "brief: publish 2026-09-15"]
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
