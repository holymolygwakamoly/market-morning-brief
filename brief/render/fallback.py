"""실패 폴백 — 기존 index.html의 <header id="status-banner">를 에러 배너로 통째 교체(AC-18)."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from brief.render.context import RunStatus
from brief.render.render import env, error_banner_text, warn_banner_text, write_status

HEADER_RE = re.compile(r"<header id=\"status-banner\"[^>]*>.*?</header>", re.DOTALL)
GENERATED_RE = re.compile(r"<html[^>]*\bdata-generated=\"([^\"]*)\"")


def write_error_banner(
    docs_dir: Path,
    *,
    reason: str,
    now_kst: datetime,
    date: str,
    status: RunStatus | None = None,
) -> Path:
    docs_dir = Path(docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)
    index = docs_dir / "index.html"

    if status is None:
        status = RunStatus(date=date, run_kind="fallback", started_at_kst=now_kst.isoformat())
    status.result = "failed"
    status.error = status.error or reason
    status.finished_at_kst = now_kst.isoformat()

    if index.exists():
        html = index.read_text(encoding="utf-8")
    else:
        html = env.get_template("empty.html.j2").render(
            date=date, error_banner=None, warn_banner=None, failed_on=None
        )

    m = GENERATED_RE.search(html)
    shown_date = m.group(1) if m else date
    header = env.get_template("_header.html.j2").render(
        failed_on=date,
        error_banner=error_banner_text(date, now_kst.strftime("%H:%M"), shown_date),
        warn_banner=warn_banner_text(status) if status.sources or status.failed_sources else None,
    )
    if HEADER_RE.search(html):
        html = HEADER_RE.sub(lambda _m: header, html, count=1)
    else:
        html = re.sub(r"(<body[^>]*>)", lambda _m: _m.group(1) + "\n" + header, html, count=1)

    index.write_text(html, encoding="utf-8")
    write_status(docs_dir, status)
    return index
