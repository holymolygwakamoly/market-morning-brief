"""렌더 계층 — Jinja 환경, 필터, 배너, 수치 대조, docs/ 출력."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup, escape

from brief.config import KST
from brief.render.context import RunStatus

if TYPE_CHECKING:  # 런타임 import 회피 — fallback CLI가 pydantic/anthropic 없이 뜨도록
    from brief.analyze.schemas import Report
    from brief.analyze.select import Selected
    from brief.collect.base import Article, Quote

PCT_RE = re.compile(r"[-+]?\d+(?:\.\d+)?\s?%")

env = Environment(
    loader=PackageLoader("brief.render", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
)


# --- 필터 ---------------------------------------------------------------------


def paragraphs(text: str | None) -> Markup:
    """이스케이프 후 빈 줄 → <p>, 단일 줄바꿈 → <br>."""
    if not text:
        return Markup("")
    s = str(escape(str(text).replace("\r\n", "\n").replace("\r", "\n"))).strip()
    parts = [p.strip() for p in re.split(r"\n\s*\n", s) if p.strip()]
    return Markup("".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in parts))


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def kst(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if value is None:
        return "-"
    return value.astimezone(KST).strftime(fmt)


env.filters["paragraphs"] = paragraphs
env.filters["pct"] = pct
env.filters["kst"] = kst


# --- 배너 ---------------------------------------------------------------------


def error_banner_text(date: str, hhmm: str, shown_date: str | None) -> str:
    text = f"오늘({date}) 보고서 생성 실패 — {hhmm} KST."
    if shown_date and shown_date != date:
        text += f" 아래는 {shown_date} 보고서입니다."
    return text


def warn_banner_text(status: RunStatus) -> str | None:
    parts: list[str] = []
    if status.failed_sources:
        parts.append("누락 소스: " + ", ".join(status.failed_sources))
    if not status.coverage_ok:
        cats_present = {s.get("category") for s in status.sources if s.get("ok") and s.get("category")}
        has_cat = any("category" in s for s in status.sources)
        missing = [c for c in ("US", "KR", "MACRO") if c not in cats_present] if has_cat else []
        parts.append("카테고리 누락: " + (", ".join(missing) if missing else "US/KR/MACRO 커버리지 미달"))
    if status.stage1_degraded:
        parts.append("1차 태깅 실패로 휴리스틱 선별(degraded)")
    if "ai_sector_short" in status.warnings:
        parts.append("AI 섹터 분량 미달")
    return " · ".join(parts) if parts else None


def _hhmm(status: RunStatus) -> str:
    for iso in (status.finished_at_kst, status.started_at_kst):
        if iso:
            try:
                return datetime.fromisoformat(iso).strftime("%H:%M")
            except ValueError:
                continue
    return datetime.now(KST).strftime("%H:%M")


def banner_context(status: RunStatus, date: str, shown_date: str | None = None) -> dict:
    failed = status.result == "failed"
    return {
        "error_banner": error_banner_text(date, _hhmm(status), shown_date) if failed else None,
        "warn_banner": warn_banner_text(status),
        "failed_on": date if failed else None,
    }


# --- 수치 대조 ----------------------------------------------------------------


def report_text_fields(report: "Report") -> list[str]:
    out: list[str] = list(report.headline5) + list(report.signal_comments)
    for s in report.sectors:
        out += [s.reason, s.us_view or "", s.kr_impact or ""]
        out += [l.comment for l in s.leaders]
    out += [report.ai_sector, report.macro_notes]
    return out


def verify_numbers(report: "Report", quotes: list["Quote"], articles: list["Article"]) -> list[str]:
    """LLM 텍스트의 % 수치 중 Quote/기사에 근거 없는 리터럴 목록."""
    allowed = {abs(round(q.change_pct, 1)) for q in quotes}
    corpus = "\n".join(f"{a.title}\n{a.summary}" for a in articles)
    unverified: list[str] = []
    for text in report_text_fields(report):
        for m in PCT_RE.finditer(text or ""):
            literal = m.group(0)
            try:
                num = abs(round(float(literal.rstrip("%").strip()), 1))
            except ValueError:
                num = None
            if num in allowed or literal in corpus:
                continue
            if literal not in unverified:
                unverified.append(literal)
    return unverified


def _ac7_lengths(report: "Report") -> tuple[int, int]:
    sector = sum(
        len(s.reason or "") + len(s.us_view or "") + len(s.kr_impact or "") for s in report.sectors
    ) + len(report.ai_sector or "")
    leaders = sum(len(l.comment or "") for s in report.sectors for l in s.leaders)
    return sector, leaders


# --- 출력 ---------------------------------------------------------------------


def write_status(docs_dir: Path, status: RunStatus) -> Path:
    p = docs_dir / "status" / f"{status.date}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(status.to_json(), encoding="utf-8")
    return p


def render_archive(docs_dir: Path) -> Path:
    reports_dir = docs_dir / "reports"
    entries = []
    for f in sorted(reports_dir.glob("*.html"), reverse=True) if reports_dir.exists() else []:
        d = f.stem
        result = None
        sp = docs_dir / "status" / f"{d}.json"
        if sp.exists():
            try:
                result = json.loads(sp.read_text(encoding="utf-8")).get("result")
            except (ValueError, OSError):
                result = None
        entries.append({"date": d, "result": result})
    html = env.get_template("archive.html.j2").render(
        date=entries[0]["date"] if entries else "",
        entries=entries,
        error_banner=None,
        warn_banner=None,
        failed_on=None,
    )
    out = docs_dir / "archive.html"
    out.write_text(html, encoding="utf-8")
    return out


def render_report(
    report: "Report | None",
    *,
    status: RunStatus,
    quotes: list["Quote"],
    quote_errors: list[str],
    selected: "Selected | None",
    docs_dir: Path,
    date: str,
) -> Path:
    docs_dir = Path(docs_dir)
    articles = (list(selected.articles) + list(selected.thebell)) if selected else []

    if report is not None:
        unverified = verify_numbers(report, quotes, articles)
        status.unverified_numbers = unverified
        if unverified and "unverified_numbers" not in status.warnings:
            status.warnings.append("unverified_numbers")
        sector_len, leader_len = _ac7_lengths(report)
        if sector_len <= leader_len and "sector_text_short" not in status.warnings:
            status.warnings.append("sector_text_short")

    ctx = {
        "date": date,
        "status": status,
        "report": report,
        "quotes": quotes,
        "quote_errors": quote_errors,
        "groups": selected.groups if selected else {},
        "thebell": selected.thebell if selected else [],
        **banner_context(status, date),
    }
    if report is not None:
        ctx["sector_groups"] = {
            k: [s for s in report.sectors if s.direction == k] for k in ("issue", "up", "down")
        }
        html = env.get_template("report.html.j2").render(**ctx)
    else:
        html = env.get_template("empty.html.j2").render(**ctx)

    reports_dir = docs_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"{date}.html"
    out.write_text(html, encoding="utf-8")
    shutil.copyfile(out, docs_dir / "index.html")
    write_status(docs_dir, status)
    render_archive(docs_dir)
    return out
