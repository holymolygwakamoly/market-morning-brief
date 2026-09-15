"""로컬 대시보드 서버 — `python -m brief.serve [--port] [--docs] [--no-browser]` (PLAN §10, US-010/011).

127.0.0.1:{port} 에서 stdlib HTTP 서버를 띄운다. 라우트:
  GET  /               대시보드(dashboard.html.j2)
  GET  /api/status     진행 상태 JSON(단계·로그 tail·결과·최근 보고서)
  POST /api/generate   {"date": "YYYY-MM-DD"} → 202 / 400(범위·형식) / 409(생성 중)
  POST /api/publish    git add docs → commit → pull --rebase → push (3회 재시도)
  GET  /docs/<path>    docs/ 정적 파일(경로 이탈 시 403)
동시 생성은 1개로 제한하며, 생성 스레드는 `brief.run.run(args)`를 그대로 호출한다.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import threading
import webbrowser
from collections import deque
from collections.abc import Callable
from datetime import date as _date
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from brief.config import KST
from brief.render.render import env

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAGE_PREFIX = "stage: "
MAX_PAST_DAYS = 3
RECENT_LIMIT = 30
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


class Busy(RuntimeError):
    """이미 생성이 진행 중."""


# --- 상태 ---------------------------------------------------------------------


class JobState:
    """생성 작업 상태(락 보호). 서버 인스턴스당 1개."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.date: str | None = None
        self.stage: str | None = None
        self.log: deque[str] = deque(maxlen=200)
        self.result: str | None = None
        self.warnings: list[str] = []
        self.report_url: str | None = None
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.error: str | None = None

    def append_log(self, line: str) -> None:
        with self.lock:
            self.log.append(line)

    def set_stage(self, stage: str) -> None:
        with self.lock:
            self.stage = stage

    def snapshot(self, log_n: int = 50) -> dict[str, Any]:
        with self.lock:
            end = self.finished_at or datetime.now(KST)
            elapsed = (end - self.started_at).total_seconds() if self.started_at else 0.0
            return {
                "running": self.running,
                "date": self.date,
                "stage": self.stage,
                "log": list(self.log)[-log_n:],
                "result": self.result,
                "warnings": list(self.warnings),
                "report_url": self.report_url,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "finished_at": self.finished_at.isoformat() if self.finished_at else None,
                "elapsed_s": round(elapsed, 1),
                "error": self.error,
            }


class LogCapture(logging.Handler):
    """로그 레코드를 state.log 에 적재하고 `stage: <name>` 메시지로 단계를 갱신한다."""

    def __init__(self, state: JobState) -> None:
        super().__init__(level=logging.INFO)
        self.state = state
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if msg.startswith(STAGE_PREFIX):
                self.state.set_stage(msg[len(STAGE_PREFIX):].strip())
            self.state.append_log(self.format(record))
        except Exception:  # noqa: BLE001 — 로깅 핸들러는 절대 예외를 전파하지 않는다
            self.handleError(record)


# --- 날짜 ---------------------------------------------------------------------


def allowed_dates(now_kst: datetime | None = None) -> tuple[_date, _date]:
    today = (now_kst or datetime.now(KST)).astimezone(KST).date()
    return today - timedelta(days=MAX_PAST_DAYS), today


def validate_date(s: Any, now_kst: datetime | None = None) -> _date | str:
    """유효하면 date, 아니면 오류 메시지(str)."""
    if not isinstance(s, str) or not DATE_RE.match(s):
        return "날짜 형식은 YYYY-MM-DD 여야 합니다."
    try:
        d = _date.fromisoformat(s)
    except ValueError:
        return "존재하지 않는 날짜입니다."
    lo, hi = allowed_dates(now_kst)
    if d < lo or d > hi:
        return f"날짜는 {lo.isoformat()} ~ {hi.isoformat()} (오늘 KST 기준 3일 전까지) 사이여야 합니다."
    return d


# --- 생성 ---------------------------------------------------------------------


def _read_status(docs_dir: Path, date: str) -> dict:
    p = docs_dir / "status" / f"{date}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _run_job(state: JobState, args: argparse.Namespace, run_fn: Callable[..., Any], docs_dir: Path) -> None:
    handler = LogCapture(state)
    root = logging.getLogger()
    root.addHandler(handler)
    brief_logger = logging.getLogger("brief")
    if brief_logger.getEffectiveLevel() > logging.INFO:
        brief_logger.setLevel(logging.INFO)
    date = args.date
    try:
        run_fn(args)
        st = _read_status(docs_dir, date)
        report = docs_dir / "reports" / f"{date}.html"
        with state.lock:
            state.result = st.get("result") or ("success" if report.exists() else "failed")
            state.warnings = list(st.get("warnings") or [])
            state.error = st.get("error") if state.result == "failed" else None
            state.report_url = f"/docs/reports/{date}.html" if report.exists() else None
    except BaseException as e:  # noqa: BLE001 — 스레드 종료 방지, 오류는 상태로 전달
        logger.error("generation crashed: %r", e)
        with state.lock:
            state.result = "failed"
            state.error = f"{type(e).__name__}: {e}"[:300]
    finally:
        root.removeHandler(handler)
        with state.lock:
            state.running = False
            state.finished_at = datetime.now(KST)
            state.stage = "done" if state.result != "failed" else "failed"


def start_generation(
    date: str,
    *,
    run_fn: Callable[..., Any] | None = None,
    docs_dir: Path,
    state: JobState,
) -> None:
    """생성 스레드를 시작한다. 진행 중이면 Busy."""
    from brief.run import parse_args, run  # 지연 import — 서버 기동 시 분석 계층 의존을 미룬다

    fn = run_fn or run
    args = parse_args(["--date", date, "--docs", str(docs_dir), "--run-kind", "manual"])
    with state.lock:
        if state.running:
            raise Busy(f"이미 생성 중입니다: {state.date}")
        state.running = True
        state.date = date
        state.stage = "start"
        state.log.clear()
        state.result = None
        state.warnings = []
        state.report_url = None
        state.error = None
        state.started_at = datetime.now(KST)
        state.finished_at = None
    t = threading.Thread(target=_run_job, args=(state, args, fn, Path(docs_dir)), name=f"brief-run-{date}", daemon=True)
    t.start()


# --- 게시 ---------------------------------------------------------------------


def _git(git_runner: Callable[..., Any], cwd: Path, *argv: str) -> tuple[int, str]:
    try:
        cp = git_runner(
            ["git", *argv], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except OSError as e:  # git 미설치 등
        return 127, f"{type(e).__name__}: {e}"
    out = ((cp.stdout or "") + (cp.stderr or "")).strip()
    return cp.returncode, out


def can_publish(docs_dir: Path, *, git_runner: Callable[..., Any] = subprocess.run) -> bool:
    rc, _ = _git(git_runner, Path(docs_dir).resolve().parent, "remote", "get-url", "origin")
    return rc == 0


def publish(
    docs_dir: Path,
    *,
    git_runner: Callable[..., Any] = subprocess.run,
    now_kst: datetime | None = None,
) -> dict[str, Any]:
    """docs/ 를 커밋하고 origin main 에 push. {"ok": bool, "output": str}."""
    docs_dir = Path(docs_dir).resolve()
    root = docs_dir.parent
    lines: list[str] = []

    def git(*argv: str) -> int:
        rc, out = _git(git_runner, root, *argv)
        lines.append(f"$ git {' '.join(argv)}" + (f"\n{out}" if out else "") + (f"\n(exit {rc})" if rc else ""))
        return rc

    def output() -> str:
        return "\n".join(lines)

    if git("add", docs_dir.name) != 0:
        return {"ok": False, "output": output()}
    if git("diff", "--cached", "--quiet") == 0:
        return {"ok": True, "output": "no changes"}
    today = (now_kst or datetime.now(KST)).astimezone(KST).date().isoformat()
    if git("commit", "-m", f"brief: publish {today}") != 0:
        return {"ok": False, "output": output()}
    for attempt in range(3):
        if attempt:
            git("rebase", "--abort")
        if git("pull", "--rebase", "origin", "main") == 0 and git("push", "origin", "HEAD:main") == 0:
            return {"ok": True, "output": output()}
    return {"ok": False, "output": output()}


# --- 서버 ---------------------------------------------------------------------


def recent_reports(docs_dir: Path, limit: int = RECENT_LIMIT) -> list[dict[str, Any]]:
    reports_dir = Path(docs_dir) / "reports"
    if not reports_dir.exists():
        return []
    files = sorted((f for f in reports_dir.glob("*.html") if DATE_RE.match(f.stem)), key=lambda f: f.stem, reverse=True)
    out = []
    for f in files[:limit]:
        st = _read_status(docs_dir, f.stem)
        out.append({"date": f.stem, "url": f"/docs/reports/{f.stem}.html", "result": st.get("result")})
    return out


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # 포트 점유 여부로 "이미 실행 중"을 판정한다

    def __init__(
        self,
        address: tuple[str, int],
        docs_dir: Path,
        *,
        run_fn: Callable[..., Any] | None = None,
        git_runner: Callable[..., Any] = subprocess.run,
        state: JobState | None = None,
    ) -> None:
        self.docs_dir = Path(docs_dir).resolve()
        self.run_fn = run_fn
        self.git_runner = git_runner
        self.state = state or JobState()
        super().__init__(address, DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    protocol_version = "HTTP/1.1"

    # -- 응답 헬퍼
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), CONTENT_TYPES[".json"])

    def _html(self, code: int, html: str) -> None:
        self._send(code, html.encode("utf-8"), CONTENT_TYPES[".html"])

    def _read_json_body(self) -> Any:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n > 0 else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — 시그니처 고정
        logger.debug("%s - %s", self.address_string(), format % args)

    # -- 라우팅
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/":
            return self._dashboard()
        if path == "/api/status":
            return self._json(HTTPStatus.OK, self._status())
        if path.startswith("/docs/"):
            return self._static(path[len("/docs/"):])
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/generate":
            return self._generate()
        if path == "/api/publish":
            return self._json(HTTPStatus.OK, publish(self.server.docs_dir, git_runner=self.server.git_runner))
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    # -- 핸들러
    def _status(self) -> dict[str, Any]:
        snap = self.server.state.snapshot()
        snap["recent"] = recent_reports(self.server.docs_dir)
        return snap

    def _dashboard(self) -> None:
        lo, hi = allowed_dates()
        html = env.get_template("dashboard.html.j2").render(
            min_date=lo.isoformat(),
            max_date=hi.isoformat(),
            today=hi.isoformat(),
            recent=recent_reports(self.server.docs_dir),
            state=self.server.state.snapshot(),
            can_publish=can_publish(self.server.docs_dir, git_runner=self.server.git_runner),
        )
        self._html(HTTPStatus.OK, html)

    def _generate(self) -> None:
        try:
            body = self._read_json_body()
        except (ValueError, UnicodeDecodeError):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "JSON 본문이 잘못되었습니다."})
        date_s = body.get("date") if isinstance(body, dict) else None
        v = validate_date(date_s)
        if isinstance(v, str):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": v})
        try:
            start_generation(
                v.isoformat(), run_fn=self.server.run_fn, docs_dir=self.server.docs_dir, state=self.server.state
            )
        except Busy as e:
            return self._json(HTTPStatus.CONFLICT, {"error": str(e)})
        self._json(HTTPStatus.ACCEPTED, {"ok": True, "date": v.isoformat()})

    def _static(self, rel: str) -> None:
        root = self.server.docs_dir
        rel = unquote(rel)
        if "\x00" in rel or rel.startswith(("/", "\\")):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
        try:
            target = (root / rel).resolve()
        except (OSError, ValueError):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        if not target.is_relative_to(root):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
        if not target.is_file():
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)


def make_server(
    docs_dir: Path | str,
    port: int,
    *,
    host: str = "127.0.0.1",
    run_fn: Callable[..., Any] | None = None,
    git_runner: Callable[..., Any] = subprocess.run,
) -> DashboardServer:
    """바인딩된 서버를 돌려준다(port=0 → 임시 포트, `server.server_address[1]`로 확인)."""
    docs = Path(docs_dir)
    docs.mkdir(parents=True, exist_ok=True)
    return DashboardServer((host, port), docs, run_fn=run_fn, git_runner=git_runner)


# --- CLI ----------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="brief.serve", description="시장 아침 브리핑 로컬 대시보드")
    p.add_argument("--port", type=int, default=int(os.getenv("BRIEF_PORT", DEFAULT_PORT)))
    p.add_argument("--docs", default="docs", help="출력 디렉터리 (기본 docs)")
    p.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    url = f"http://127.0.0.1:{args.port}/"
    try:
        server = make_server(args.docs, args.port)
    except OSError as e:
        print(f"이미 실행 중입니다({e}). 브라우저에서 {url} 를 엽니다.")
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if not args.no_browser:
        webbrowser.open(url)
    print(f"대시보드: {url}  (Ctrl+C 로 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("종료합니다.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
