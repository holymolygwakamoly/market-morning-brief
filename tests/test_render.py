"""Step 4 렌더 계층 테스트 — 템플릿 구조(AC-10/11/12), 배너(AC-17/18), AC-7, XSS, 수치 대조, 폴백."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from brief.analyze.schemas import Report
from brief.analyze.select import Selected
from brief.collect.base import Article, Quote
from brief.config import KST
from brief.render.context import RunStatus
from brief.render.fallback import write_error_banner
from brief.render.render import render_report, verify_numbers

FIXTURES = Path(__file__).parent / "fixtures"
VALID_REPORT = json.loads((FIXTURES / "report_valid.json").read_text(encoding="utf-8"))
DATE = "2026-09-15"
XSS_TITLE = "<script>alert(1)</script>"


# --- helpers -----------------------------------------------------------------


def _report(**overrides) -> Report:
    return Report(**{**VALID_REPORT, **overrides})


def _quotes() -> list[Quote]:
    return [
        Quote(symbol="^GSPC", name="S&P 500", close=6500.12, change_pct=0.45,
              asof=datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc), prev_close=6471.0),
        Quote(symbol="^KS11", name="KOSPI", close=3400.5, change_pct=-1.2,
              asof=datetime(2026, 9, 15, 6, 30, tzinfo=timezone.utc)),
    ]


def _article(i: int, **kw) -> Article:
    d = dict(title=f"기사 {i}", url=f"https://ex.com/{i}",
             published_at=datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc),
             source="연합", category="KR", lang="ko", summary=f"요약 {i}")
    d.update(kw)
    return Article(**d)


def _selected() -> Selected:
    a1 = _article(1)
    a2 = _article(2, title=XSS_TITLE, source="CNBC Finance", category="US", lang="en", publisher="CNBC")
    a3 = _article(3, source="더벨 (Google News)")
    return Selected(articles=[a1, a2], tags=None, thebell=[a3], groups={"k1": [a1], "k2": [a2]})


def _status(**kw) -> RunStatus:
    base = dict(
        date=DATE, run_kind="main",
        started_at_kst=datetime(2026, 9, 15, 6, 50, tzinfo=KST).isoformat(),
        finished_at_kst=datetime(2026, 9, 15, 6, 58, tzinfo=KST).isoformat(),
        result="success",
        sources=[
            {"name": "연합", "ok": True, "count": 10, "error": None, "required": False},
            {"name": "더벨 (Google News)", "ok": True, "count": 5, "error": None, "required": True},
        ],
        failed_sources=[], coverage_ok=True,
    )
    base.update(kw)
    return RunStatus(**base)


def _render(tmp_path: Path, *, report=None, status=None, quotes=None, quote_errors=None,
            selected=None, date=DATE) -> tuple[Path, str]:
    p = render_report(
        report if report is not None else _report(),
        status=status or _status(),
        quotes=_quotes() if quotes is None else quotes,
        quote_errors=quote_errors or [],
        selected=selected if selected is not None else _selected(),
        docs_dir=tmp_path,
        date=date,
    )
    return p, p.read_text(encoding="utf-8")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# --- 구조 (AC-10/11/12) ---------------------------------------------------------


def test_three_sections_in_order_and_html_attrs(tmp_path):
    _, html = _render(tmp_path)
    soup = _soup(html)
    ids = [s["id"] for s in soup.find_all("section") if s.get("id") in ("summary", "analysis", "references")]
    assert ids == ["summary", "analysis", "references"]
    assert soup.html["lang"] == "ko"
    assert soup.html["data-generated"] == DATE
    assert soup.find("meta", attrs={"name": "viewport"}) is not None
    head = html.split("<body>")[0]
    assert "http" not in head  # 인라인 CSS, 외부 URL 없음
    ai = soup.find("section", id="ai-sector")
    assert ai is not None and ai.find_parent("section")["id"] == "analysis"


def test_summary_is_first_child_of_main_and_banner_outside(tmp_path):
    _, html = _render(tmp_path)
    soup = _soup(html)
    main = soup.find("main")
    first = next(c for c in main.children if getattr(c, "name", None))
    assert first.name == "section" and first["id"] == "summary"
    header = soup.find("header", id="status-banner")
    assert header is not None
    assert header.find_parent("main") is None
    stale = header.find("div", id="stale-banner")
    assert stale is not None and stale.has_attr("hidden")


def test_outputs_written(tmp_path):
    p, html = _render(tmp_path)
    assert p == tmp_path / "reports" / f"{DATE}.html"
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == html
    status = json.loads((tmp_path / "status" / f"{DATE}.json").read_text(encoding="utf-8"))
    assert status["result"] == "success" and status["date"] == DATE
    archive = (tmp_path / "archive.html").read_text(encoding="utf-8")
    assert f'href="reports/{DATE}.html"' in archive
    assert "success" in archive


def test_index_is_latest_date_not_last_rendered(tmp_path):
    """index.html = 날짜가 가장 최신인 보고서 — 과거 날짜를 나중에 생성해도 최신 index를 덮지 않는다."""
    _render(tmp_path, date="2026-09-16", status=_status(date="2026-09-16"))
    _render(tmp_path, date="2026-09-14", status=_status(date="2026-09-14"))
    assert (tmp_path / "reports" / "2026-09-14.html").exists()
    index = _soup((tmp_path / "index.html").read_text(encoding="utf-8"))
    assert index.html["data-generated"] == "2026-09-16"


def test_references_lists_sources_and_articles(tmp_path):
    _, html = _render(tmp_path)
    soup = _soup(html)
    refs = soup.find("section", id="references")
    src_table = refs.select_one("table.sources")
    assert "더벨 (Google News)" in src_table.get_text()
    assert "✓" in src_table.get_text()
    assert "더벨 헤드라인 (Google News)" in refs.get_text()
    assert len(refs.select("ul.articles > li")) == 2  # story_key 그룹 2개
    assert 'href="https://ex.com/3"' in html


# --- 배너 (AC-1/17) --------------------------------------------------------------


def test_warn_banner_lists_all_failed_sources(tmp_path):
    failed = ["더벨 (Google News)", "CNBC Finance"]
    _, html = _render(tmp_path, status=_status(failed_sources=failed, result="degraded"))
    soup = _soup(html)
    warns = soup.select("#status-banner .banner-warn:not(.stale)")
    assert len(warns) == 1
    for name in failed:
        assert name in warns[0].get_text()
    assert not soup.select(".banner-error")


def test_no_banner_when_healthy(tmp_path):
    _, html = _render(tmp_path)
    soup = _soup(html)
    assert not soup.select("#status-banner .banner-warn:not(.stale)")
    assert not soup.select(".banner-error")
    assert soup.find("header", id="status-banner").get("data-failed-on") is None


def test_warn_banner_coverage_and_ai_short(tmp_path):
    st = _status(coverage_ok=False, stage1_degraded=True, warnings=["ai_sector_short"])
    _, html = _render(tmp_path, status=st)
    warns = _soup(html).select("#status-banner .banner-warn:not(.stale)")
    assert len(warns) == 1
    text = warns[0].get_text()
    assert "카테고리 누락" in text and "AI 섹터 분량 미달" in text and "degraded" in text


# --- AC-7 ---------------------------------------------------------------------


def test_sector_text_longer_than_leaders(tmp_path):
    _, html = _render(tmp_path)
    soup = _soup(html)
    sector_chars = sum(len(e.get_text(strip=True)) for e in soup.select(".sector-text"))
    leader_chars = sum(len(e.get_text(strip=True)) for e in soup.select("ul.leaders"))
    assert sector_chars > leader_chars


# --- XSS ----------------------------------------------------------------------


def test_xss_title_escaped(tmp_path):
    _, html = _render(tmp_path)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert" not in html
    assert html.count("<script") == 1


def test_paragraphs_filter_escapes_and_breaks(tmp_path):
    rep = _report(macro_notes="첫 줄 <b>굵게</b>\n둘째 줄\n\n새 문단")
    _, html = _render(tmp_path, report=rep)
    assert "<p>첫 줄 &lt;b&gt;굵게&lt;/b&gt;<br>둘째 줄</p><p>새 문단</p>" in html


# --- 시그널 표 ------------------------------------------------------------------


def test_signals_table_from_quotes(tmp_path):
    _, html = _render(tmp_path, quote_errors=["^DJI: timeout"])
    table = _soup(html).select_one("#summary table.signals")
    rows = table.select("tbody tr")
    assert len(rows) == 3
    c0 = [td.get_text(" ", strip=True) for td in rows[0].find_all("td")]
    assert c0[0].startswith("S&P 500") and c0[1] == "6,500.12" and c0[2] == "+0.45%"
    assert "up" in rows[0].find_all("td")[2]["class"]
    c1 = [td.get_text(" ", strip=True) for td in rows[1].find_all("td")]
    assert c1[1] == "3,400.50" and c1[2] == "-1.20%"
    assert "down" in rows[1].find_all("td")[2]["class"]
    assert "데이터 없음" in rows[2].get_text()
    assert "09-16 05:00 KST" in c0[0]  # asof 배지(KST)


# --- 수치 대조 ------------------------------------------------------------------


def test_verify_numbers_flags_unknown_pct(tmp_path):
    rep = _report(macro_notes="코스피가 3.2% 올랐다.")
    st = _status()
    _render(tmp_path, report=rep, status=st)
    assert st.unverified_numbers == ["3.2%"]
    assert "unverified_numbers" in st.warnings
    saved = json.loads((tmp_path / "status" / f"{DATE}.json").read_text(encoding="utf-8"))
    assert saved["unverified_numbers"] == ["3.2%"]


def test_verify_numbers_allows_quote_and_article_match():
    rep = _report(macro_notes="코스피가 3.2% 올랐다. 환율 0.7% 상승.")
    q = [Quote(symbol="^KS11", name="KOSPI", close=1.0, change_pct=3.21,
               asof=datetime(2026, 9, 15, tzinfo=timezone.utc))]
    assert verify_numbers(rep, q, []) == ["0.7%"]
    arts = [_article(9, summary="환율 0.7% 상승")]
    assert verify_numbers(rep, q, arts) == []
    assert verify_numbers(rep, [], []) == ["3.2%", "0.7%"]


# --- 폴백 (AC-18) ----------------------------------------------------------------


def test_fallback_twice_yields_single_error_banner(tmp_path):
    _render(tmp_path)
    now = datetime(2026, 9, 16, 7, 5, tzinfo=KST)
    for _ in range(2):
        out = write_error_banner(tmp_path, reason="stage2_failed", now_kst=now, date="2026-09-16")
    assert out == tmp_path / "index.html"
    soup = _soup(out.read_text(encoding="utf-8"))
    errors = soup.select(".banner-error")
    assert len(errors) == 1
    text = errors[0].get_text()
    assert "2026-09-15" in text and "2026-09-16" in text and "07:05" in text
    assert len(soup.select("header#status-banner")) == 1
    header = soup.find("header", id="status-banner")
    assert header["data-failed-on"] == "2026-09-16"
    assert header.find("div", id="stale-banner") is not None
    assert soup.html["data-generated"] == "2026-09-15"
    assert soup.find("section", id="summary") is not None  # 본문 유지
    status = json.loads((tmp_path / "status" / "2026-09-16.json").read_text(encoding="utf-8"))
    assert status["result"] == "failed" and status["error"] == "stage2_failed"


def test_fallback_first_run_renders_empty_page(tmp_path):
    now = datetime(2026, 9, 16, 7, 5, tzinfo=KST)
    out = write_error_banner(tmp_path, reason="collect_failed", now_kst=now, date="2026-09-16")
    soup = _soup(out.read_text(encoding="utf-8"))
    assert [s["id"] for s in soup.find_all("section")] == ["summary", "analysis", "references"]
    assert len(soup.select(".banner-error")) == 1
    assert "아래는" not in soup.select_one(".banner-error").get_text()
    assert soup.html["data-generated"] == "2026-09-16"
    assert "아직 생성된 보고서가 없습니다" in soup.get_text()
    saved = json.loads((tmp_path / "status" / "2026-09-16.json").read_text(encoding="utf-8"))
    assert saved["result"] == "failed"


def test_render_report_none_uses_empty_template(tmp_path):
    st = _status(result="failed", error="stage2_failed")
    p = render_report(None, status=st, quotes=[], quote_errors=[], selected=None, docs_dir=tmp_path, date=DATE)
    soup = _soup(p.read_text(encoding="utf-8"))
    assert [s["id"] for s in soup.find_all("section")] == ["summary", "analysis", "references"]
    assert len(soup.select(".banner-error")) == 1
    assert soup.find("header", id="status-banner")["data-failed-on"] == DATE


def test_fallback_cli(tmp_path):
    docs = tmp_path / "docs"
    proc = subprocess.run(
        [sys.executable, "-m", "brief.fallback", "--reason", "runner_killed", "--docs", str(docs)],
        cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert (docs / "index.html").exists()
    html = (docs / "index.html").read_text(encoding="utf-8")
    assert html.count('class="banner banner-error"') == 1
    assert list((docs / "status").glob("*.json"))


# --- RunStatus ------------------------------------------------------------------


def test_run_status_roundtrip():
    st = _status(failed_sources=["x"], llm={"total_cost_usd": 0.1}, warnings=["a"])
    back = RunStatus.from_json(st.to_json())
    assert back == st
