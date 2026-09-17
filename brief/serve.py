"""로컬 대시보드 서버 — `python -m brief.serve [--port] [--docs] [--no-browser]` (PLAN §11.6).

127.0.0.1:{port} 에서 stdlib HTTP 서버를 띄운다. 라우트:
  GET  /, /index.html   정적 SPA(docs/index.html — 템플릿에서 복사)
  GET  /api/status      작업 상태 JSON(kind·date·stage·log tail·result·today·can_publish)
  POST /api/update      {"date"} → 202 / 400(오늘 KST 아님·형식) / 409(실행 중·오늘 이미 완료 = 하루 1회)
  POST /api/regenerate  {"date","topic"} → 202 / 400 / 409(실행 중·스냅샷 없음)
  POST /api/publish     git add docs → commit → pull --rebase → push (3회 재시도)
  GET  /<path>          docs/ 정적 파일(data/…, reports/…). `/docs/<path>` 도 같은 곳(구 링크 호환). 경로 이탈 시 403
동시 작업은 1개로 제한하며, 작업 스레드는 `brief.pipeline.update/regenerate` 를 그대로 호출한다.
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
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from brief.analyze.select import TOPICS
from brief.config import KST

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAGE_PREFIX = "stage: "
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
    """이미 작업이 진행 중."""


class AlreadyDone(RuntimeError):
    """오늘 업데이트가 이미 완료됨(하루 1회)."""


# --- 상태 ---------------------------------------------------------------------


class JobState:
    """작업 상태(락 보호). 서버 인스턴스당 1개."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.kind: str | None = None  # update | regenerate
        self.date: str | None = None
        self.topics: list[str] = []
        self.stage: str | None = None
        self.log: deque[str] = deque(maxlen=300)
        self.result: str | None = None
        self.error: str | None = None
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None

    def append_log(self, line: str) -> None:
        with self.lock:
            self.log.append(line)

    def set_stage(self, stage: str) -> None:
        with self.lock:
            self.stage = stage

    def snapshot(self, log_n: int = 60) -> dict[str, Any]:
        with self.lock:
            end = self.finished_at or datetime.now(KST)
            elapsed = (end - self.started_at).total_seconds() if self.started_at else 0.0
            return {
                "running": self.running,
                "kind": self.kind,
                "date": self.date,
                "topics": list(self.topics),
                "stage": self.stage,
                "log": list(self.log)[-log_n:],
                "result": self.result,
                "error": self.error,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "finished_at": self.finished_at.isoformat() if self.finished_at else None,
                "elapsed_s": round(elapsed, 1),
            }


class LogCapture(logging.Handler):
    """로그 레코드를 state.log 에 적재하고 `stage: <name>` 메시지로 단계를 갱신한다."""

    def __init__(self, state: JobState) -> None:
        super().__init__(level=logging.INFO)
        self.state = state
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith("httpx"):
                return
            msg = record.getMessage()
            if msg.startswith(STAGE_PREFIX):
                self.state.set_stage(msg[len(STAGE_PREFIX):].strip())
            self.state.append_log(self.format(record))
        except Exception:  # noqa: BLE001 — 로깅 핸들러는 절대 예외를 전파하지 않는다
            self.handleError(record)


# --- 날짜 ---------------------------------------------------------------------


def today_kst(now_kst: datetime | None = None) -> _date:
    return (now_kst or datetime.now(KST)).astimezone(KST).date()


def validate_date(s: Any, now_kst: datetime | None = None) -> _date | str:
    """오늘(KST)만 허용. 유효하면 date, 아니면 오류 메시지(str)."""
    if not isinstance(s, str) or not DATE_RE.match(s):
        return "날짜 형식은 YYYY-MM-DD 여야 합니다."
    try:
        d = _date.fromisoformat(s)
    except ValueError:
        return "존재하지 않는 날짜입니다."
    t = today_kst(now_kst)
    if d != t:
        return f"업데이트는 오늘(KST {t.isoformat()}) 기준일만 가능합니다. 지난 날짜는 그날 업데이트한 내역만 볼 수 있습니다."
    return d


# --- 작업 ---------------------------------------------------------------------


def _run_job(state: JobState, fn: Callable[[], Any], docs_dir: Path, date: str) -> None:
    from brief.pipeline import read_status  # noqa: PLC0415

    handler = LogCapture(state)
    root = logging.getLogger()
    root.addHandler(handler)
    brief_logger = logging.getLogger("brief")
    if brief_logger.getEffectiveLevel() > logging.INFO:
        brief_logger.setLevel(logging.INFO)
    try:
        fn()
        st = read_status(docs_dir, date)
        with state.lock:
            state.result = st.get("result") or "failed"
            state.error = st.get("error")
    except BaseException as e:  # noqa: BLE001 — 스레드 종료 방지, 오류는 상태로 전달
        logger.error("job crashed: %r", e)
        with state.lock:
            state.result = "failed"
            state.error = f"{type(e).__name__}: {e}"[:300]
    finally:
        root.removeHandler(handler)
        with state.lock:
            state.running = False
            state.finished_at = datetime.now(KST)
            state.stage = "done" if state.result != "failed" else "failed"


def _begin(state: JobState, kind: str, date: str, topics: list[str]) -> None:
    with state.lock:
        if state.running:
            raise Busy(f"이미 작업 중입니다: {state.kind} {state.date}")
        state.running = True
        state.kind, state.date, state.topics = kind, date, topics
        state.stage = "start"
        state.log.clear()
        state.result = None
        state.error = None
        state.started_at = datetime.now(KST)
        state.finished_at = None


def start_update(date: str, *, docs_dir: Path, state: JobState, update_fn: Callable[..., Any] | None = None) -> None:
    """업데이트 스레드 시작. 진행 중이면 Busy, 오늘 이미 success/degraded면 AlreadyDone(하루 1회)."""
    from brief.pipeline import is_done, update  # noqa: PLC0415 — 서버 기동 시 분석 계층 의존을 미룬다

    fn = update_fn or update
    if is_done(docs_dir, date):
        raise AlreadyDone("오늘 업데이트는 이미 완료되었습니다(하루 1회). 보고서가 마음에 들지 않으면 탭별 [재생성]을 사용하세요.")
    _begin(state, "update", date, list(TOPICS))
    t = threading.Thread(target=_run_job, args=(state, lambda: fn(date, docs_dir), Path(docs_dir), date), name=f"brief-update-{date}", daemon=True)
    t.start()


def start_regenerate(date: str, topic: str, *, docs_dir: Path, state: JobState, regen_fn: Callable[..., Any] | None = None) -> None:
    from brief.pipeline import regenerate  # noqa: PLC0415

    fn = regen_fn or regenerate
    if not (Path(docs_dir) / "data" / date / "inputs.json").exists():
        raise FileNotFoundError(f"{date} 업데이트 입력이 없어 재생성할 수 없습니다. 먼저 [업데이트]를 실행하세요.")
    _begin(state, "regenerate", date, [topic])
    t = threading.Thread(target=_run_job, args=(state, lambda: fn(date, (topic,), docs_dir), Path(docs_dir), date), name=f"brief-regen-{date}-{topic}", daemon=True)
    t.start()


# --- 게시 ---------------------------------------------------------------------


def _git(git_runner: Callable[..., Any], cwd: Path, *argv: str) -> tuple[int, str]:
    try:
        cp = git_runner(["git", *argv], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as e:  # git 미설치 등
        return 127, f"{type(e).__name__}: {e}"
    out = ((cp.stdout or "") + (cp.stderr or "")).strip()
    return cp.returncode, out


def can_publish(docs_dir: Path, *, git_runner: Callable[..., Any] = subprocess.run) -> bool:
    rc, _ = _git(git_runner, Path(docs_dir).resolve().parent, "remote", "get-url", "origin")
    return rc == 0


def publish(docs_dir: Path, *, git_runner: Callable[..., Any] = subprocess.run, now_kst: datetime | None = None) -> dict[str, Any]:
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
    if git("commit", "-m", f"dashboard: publish {today}") != 0:
        return {"ok": False, "output": output()}
    for attempt in range(3):
        if attempt:
            git("rebase", "--abort")
        if git("pull", "--rebase", "origin", "main") == 0 and git("push", "origin", "HEAD:main") == 0:
            return {"ok": True, "output": output()}
    return {"ok": False, "output": output()}


# --- 서버 ---------------------------------------------------------------------


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # 포트 점유 여부로 "이미 실행 중"을 판정한다

    def __init__(
        self,
        address: tuple[str, int],
        docs_dir: Path,
        *,
        update_fn: Callable[..., Any] | None = None,
        regen_fn: Callable[..., Any] | None = None,
        git_runner: Callable[..., Any] = subprocess.run,
        state: JobState | None = None,
    ) -> None:
        self.docs_dir = Path(docs_dir).resolve()
        self.update_fn = update_fn
        self.regen_fn = regen_fn
        self.git_runner = git_runner
        self.state = state or JobState()
        super().__init__(address, DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    protocol_version = "HTTP/1.1"

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

    def _read_json_body(self) -> Any:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n > 0 else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — 시그니처 고정
        logger.debug("%s - %s", self.address_string(), format % args)

    # -- 라우팅
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path == "/api/status":
            return self._json(HTTPStatus.OK, self._status())
        if path.startswith("/docs/"):
            return self._static(path[len("/docs/"):])
        return self._static(path.lstrip("/"))

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/update":
            return self._update()
        if path == "/api/regenerate":
            return self._regenerate()
        if path == "/api/publish":
            return self._json(HTTPStatus.OK, publish(self.server.docs_dir, git_runner=self.server.git_runner))
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    # -- 핸들러
    def _status(self) -> dict[str, Any]:
        snap = self.server.state.snapshot()
        snap["today"] = today_kst().isoformat()
        snap["can_publish"] = can_publish(self.server.docs_dir, git_runner=self.server.git_runner)
        return snap

    def _body(self) -> dict | None:
        try:
            body = self._read_json_body()
        except (ValueError, UnicodeDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "JSON 본문이 잘못되었습니다."})
            return None
        return body if isinstance(body, dict) else {}

    def _update(self) -> None:
        body = self._body()
        if body is None:
            return
        v = validate_date(body.get("date"))
        if isinstance(v, str):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": v})
        try:
            start_update(v.isoformat(), docs_dir=self.server.docs_dir, state=self.server.state, update_fn=self.server.update_fn)
        except (Busy, AlreadyDone) as e:
            return self._json(HTTPStatus.CONFLICT, {"error": str(e)})
        self._json(HTTPStatus.ACCEPTED, {"ok": True, "date": v.isoformat()})

    def _regenerate(self) -> None:
        body = self._body()
        if body is None:
            return
        v = validate_date(body.get("date"))
        if isinstance(v, str):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": v})
        topic = body.get("topic")
        if topic not in TOPICS:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": f"topic은 {', '.join(TOPICS)} 중 하나여야 합니다."})
        try:
            start_regenerate(v.isoformat(), topic, docs_dir=self.server.docs_dir, state=self.server.state, regen_fn=self.server.regen_fn)
        except Busy as e:
            return self._json(HTTPStatus.CONFLICT, {"error": str(e)})
        except FileNotFoundError as e:
            return self._json(HTTPStatus.CONFLICT, {"error": str(e)})
        self._json(HTTPStatus.ACCEPTED, {"ok": True, "date": v.isoformat(), "topic": topic})

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
    update_fn: Callable[..., Any] | None = None,
    regen_fn: Callable[..., Any] | None = None,
    git_runner: Callable[..., Any] = subprocess.run,
) -> DashboardServer:
    """바인딩된 서버를 돌려준다(port=0 → 임시 포트). 기동 시 SPA·index.json 을 준비한다."""
    from brief.pipeline import ensure_spa, write_index  # noqa: PLC0415

    docs = Path(docs_dir)
    docs.mkdir(parents=True, exist_ok=True)
    ensure_spa(docs)
    write_index(docs)
    return DashboardServer((host, port), docs, update_fn=update_fn, regen_fn=regen_fn, git_runner=git_runner)


# --- CLI ----------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="brief.serve", description="리서치 대시보드 로컬 서버")
    p.add_argument("--port", type=int, default=int(os.getenv("BRIEF_PORT", DEFAULT_PORT)))
    p.add_argument("--docs", default="docs", help="출력 디렉터리 (기본 docs)")
    p.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
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
    print(f"리서치 대시보드: {url}  (Ctrl+C 로 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("종료합니다.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
