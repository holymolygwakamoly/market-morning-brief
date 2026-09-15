"""Step 5 오케스트레이션 테스트 — 네트워크/LLM 없이 주입 가능한 페이크로 run()을 검증한다."""
from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from brief import run as run_mod
from brief.analyze.client import CallCapExceeded
from brief.analyze.schemas import Report, ReportOut, Stage1Item, Stage1Result
from brief.analyze.stage1_tag import Stage1Degraded
from brief.collect.base import Article, Quote, SourceResult
from brief.config import KST
from brief.run import is_done, parse_args, run

FIXTURES = Path(__file__).parent / "fixtures"
VALID_REPORT = json.loads((FIXTURES / "report_valid.json").read_text(encoding="utf-8"))
DATE = "2026-09-16"
NOW = datetime(2026, 9, 16, 6, 50, tzinfo=KST)
# dedupe(bigram 유사도)에 병합되지 않도록 서로 다른 제목을 순환 사용
TITLES = [
    "반도체 수출 급증세 지속", "Fed holds rates steady amid inflation", "코스피 외국인 순매수 전환",
    "Oil prices slide on supply glut", "밸류업 공시 기업 늘어", "Nvidia earnings top estimates",
    "환율 1,380원대 등락", "Treasury yields retreat after CPI", "조선업 수주 호황", "Apple unveils new chips",
]


# --- helpers -----------------------------------------------------------------


def _args(**kw) -> Namespace:
    base = dict(date=DATE, dry_run=False, max_articles=None, skip_if_done=False,
                run_kind="manual", docs="docs", sources=None)
    base.update(kw)
    return Namespace(**base)


def _article(i: int, *, source="연합뉴스 경제", category="KR", lang="ko", minutes_ago=0) -> Article:
    # 윈도: 2026-09-15 06:50 KST ~ 2026-09-16 09:50 KST. 기본은 09-16 06:00 KST 부근.
    return Article(
        title=f"{source} 제{i}호: {TITLES[i % len(TITLES)]}", url=f"https://ex.com/{i}",
        published_at=NOW - timedelta(minutes=50 + minutes_ago + i), source=source,
        category=category, lang=lang, summary=f"요약 {i}",
    )


def _results(*, fail_one=True) -> list[SourceResult]:
    kr = [_article(i) for i in range(1, 4)]
    us = [_article(i, source="CNBC Top News", category="US", lang="en") for i in range(10, 13)]
    macro = [_article(20, source="Fed 보도자료", category="MACRO", lang="en")]
    bell = [_article(30, source="더벨 (Google News)")]
    out = [
        SourceResult(name="연합뉴스 경제", ok=True, count=3, articles=kr, elapsed_s=0.5),
        SourceResult(name="CNBC Top News", ok=True, count=3, articles=us, elapsed_s=0.4),
        SourceResult(name="Fed 보도자료", ok=True, count=1, articles=macro, elapsed_s=0.3),
        SourceResult(name="더벨 (Google News)", ok=True, count=1, articles=bell, elapsed_s=0.9),
    ]
    if fail_one:
        out.append(SourceResult(name="한국경제 증권", ok=False, count=0, error="HTTP 503", elapsed_s=1.0))
    return out


def _quotes_fn(quotes_cfg, defaults):
    return (
        [Quote(symbol="^GSPC", name="S&P 500", close=6500.12, change_pct=0.45,
               asof=datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc))],
        ["^TNX: boom"],
    )


class FakeLLM:
    def __init__(self, **kw):
        self.kw = kw

    def summary(self):
        return {"stages": {"stage1": {"calls": 1}, "stage2": {"calls": 1}}, "total_cost_usd": 0.27}

    def total_cost_usd(self):
        return 0.27

    def warnings(self):
        return []


def _stage1_for(articles: list[Article]) -> Stage1Result:
    return Stage1Result(items=[
        Stage1Item(i=i, p=3, m=a.category, a=False, s=["OTHER"], k=f"k{i}") for i, a in enumerate(articles)
    ])


def _relaxed(data: dict) -> Report:
    """ai_sector 길이 검증을 우회한 Report (stage2 완화 반환과 동일 형태)."""
    out = ReportOut(**data)
    return Report.model_construct(**{f: getattr(out, f) for f in ReportOut.model_fields})


def _fail_if_called(*a, **kw):
    pytest.fail("must not be called")


def _status(docs: Path, date: str = DATE) -> dict:
    return json.loads((docs / "status" / f"{date}.json").read_text(encoding="utf-8"))


def _banners(docs: Path, cls: str) -> list:
    soup = BeautifulSoup((docs / "index.html").read_text(encoding="utf-8"), "html.parser")
    return soup.select(f".{cls}:not(.stale)")  # 클라이언트 stale 배너(hidden) 제외


@pytest.fixture
def fakes(monkeypatch):
    """비-dry 경로용: run_stage1/run_stage2를 페이크로 교체하고 공통 주입 인자를 반환."""
    monkeypatch.setattr(run_mod, "run_stage1", lambda llm, arts, *, deadline: _stage1_for(arts))
    monkeypatch.setattr(
        run_mod, "run_stage2", lambda llm, sel, q, d, *, deadline: (Report(**VALID_REPORT), [])
    )
    return dict(now=NOW, collect=lambda cfg, **kw: _results(), quotes_fn=_quotes_fn,
                llm_factory=FakeLLM, sleep=lambda s: None)


# --- parse_args / is_done -----------------------------------------------------


def test_parse_args_default_date_is_kst():
    # 2026-09-15T21:50Z == 2026-09-16 06:50 KST
    args = parse_args([], now=datetime(2026, 9, 15, 21, 50, tzinfo=timezone.utc))
    assert args.date == "2026-09-16"
    assert args.run_kind == "manual" and args.docs == "docs" and not args.dry_run


def test_parse_args_flags():
    args = parse_args(["--date", "2026-09-17", "--dry-run", "--max-articles", "5",
                       "--skip-if-done", "--run-kind", "backup", "--docs", "out"])
    assert (args.date, args.dry_run, args.max_articles, args.skip_if_done, args.run_kind, args.docs) == (
        "2026-09-17", True, 5, True, "backup", "out")


def test_is_done(tmp_path):
    assert is_done(tmp_path, DATE) is False
    (tmp_path / "status").mkdir()
    for result, expected in (("success", True), ("degraded", True), ("failed", False)):
        (tmp_path / "status" / f"{DATE}.json").write_text(json.dumps({"result": result}), encoding="utf-8")
        assert is_done(tmp_path, DATE) is expected
    (tmp_path / "status" / f"{DATE}.json").write_text("not json", encoding="utf-8")
    assert is_done(tmp_path, DATE) is False


# --- dry-run E2E ---------------------------------------------------------------


def test_dry_run_e2e(tmp_path):
    rc = run(_args(dry_run=True, docs=str(tmp_path)), now=NOW, collect=_fail_if_called,
             quotes_fn=_fail_if_called, llm_factory=_fail_if_called)
    assert rc == 0
    for rel in (f"reports/{DATE}.html", "index.html", "archive.html", f"status/{DATE}.json"):
        assert (tmp_path / rel).exists(), rel
    st = _status(tmp_path)
    assert st["result"] in ("success", "degraded")
    assert st["date"] == DATE and st["run_kind"] == "manual"
    assert st["article_count"] > 0 and st["coverage_ok"] is True
    assert st["cost_usd"] == 0.0
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert f'data-generated="{DATE}"' in html
    assert not _banners(tmp_path, "banner-error")


# --- failure paths -------------------------------------------------------------


def _raise(exc):
    def _f(*a, **kw):
        raise exc
    return _f


def test_deadline_exceeded_writes_error_banner(tmp_path):
    rc = run(_args(docs=str(tmp_path)), now=NOW, collect=_raise(TimeoutError("deadline")),
             quotes_fn=_fail_if_called, llm_factory=_fail_if_called)
    assert rc == 0
    assert len(_banners(tmp_path, "banner-error")) == 1
    st = _status(tmp_path)
    assert st["result"] == "failed" and "deadline" in st["error"]
    assert st["run_kind"] == "manual" and st["started_at_kst"] == NOW.isoformat()


def test_runtime_error_in_stage_writes_error_banner(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(run_mod, "run_stage2", _raise(RuntimeError("kaboom")))
    assert run(_args(docs=str(tmp_path)), **fakes) == 0
    assert len(_banners(tmp_path, "banner-error")) == 1
    st = _status(tmp_path)
    assert st["result"] == "failed" and st["error"].startswith("RuntimeError: kaboom")


def test_stage2_cap_exceeded_is_failure(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(run_mod, "run_stage2", _raise(CallCapExceeded("stage2", ValueError("bad"))))
    assert run(_args(docs=str(tmp_path)), **fakes) == 0
    st = _status(tmp_path)
    assert st["result"] == "failed" and "stage2_failed" in st["error"]
    assert len(_banners(tmp_path, "banner-error")) == 1


def test_keyboard_interrupt_writes_error_banner(tmp_path):
    rc = run(_args(docs=str(tmp_path)), now=NOW, collect=_raise(KeyboardInterrupt()),
             quotes_fn=_fail_if_called, llm_factory=_fail_if_called)
    assert rc == 0
    assert len(_banners(tmp_path, "banner-error")) == 1
    assert _status(tmp_path)["error"].startswith("KeyboardInterrupt")


def test_error_banner_keeps_previous_report(tmp_path, fakes):
    # 전일 성공 보고서가 있으면 실패 시 그 위에 배너만 얹는다 (data-generated 유지).
    assert run(_args(docs=str(tmp_path), date="2026-09-15"), **{**fakes, "now": NOW - timedelta(days=1)}) == 0
    assert run(_args(docs=str(tmp_path)), now=NOW, collect=_raise(TimeoutError("deadline")),
               quotes_fn=_fail_if_called, llm_factory=_fail_if_called) == 0
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'data-generated="2026-09-15"' in html
    assert len(_banners(tmp_path, "banner-error")) == 1
    assert _status(tmp_path, "2026-09-15")["result"] == "degraded"
    assert _status(tmp_path)["result"] == "failed"


# --- skip-if-done --------------------------------------------------------------


@pytest.mark.parametrize("prev,expect_called", [("success", False), ("degraded", False), ("failed", True)])
def test_skip_if_done(tmp_path, fakes, prev, expect_called):
    (tmp_path / "status").mkdir()
    (tmp_path / "status" / f"{DATE}.json").write_text(
        json.dumps({"date": DATE, "result": prev}), encoding="utf-8")
    called = []

    def collect(cfg, **kw):
        called.append(1)
        return _results()

    rc = run(_args(docs=str(tmp_path), skip_if_done=True), **{**fakes, "collect": collect})
    assert rc == 0
    assert bool(called) is expect_called
    if not expect_called:
        # 아무것도 쓰지 않는다
        assert _status(tmp_path) == {"date": DATE, "result": prev}
        assert not (tmp_path / "index.html").exists()


def test_no_skip_flag_reruns_even_if_done(tmp_path, fakes):
    (tmp_path / "status").mkdir()
    (tmp_path / "status" / f"{DATE}.json").write_text(json.dumps({"result": "success"}), encoding="utf-8")
    called = []
    fakes["collect"] = lambda cfg, **kw: (called.append(1), _results())[1]
    assert run(_args(docs=str(tmp_path)), **fakes) == 0
    assert called


# --- full non-dry path with fakes ------------------------------------------------


def test_full_path_with_fakes(tmp_path, fakes):
    llms = []

    def factory(**kw):
        llm = FakeLLM(**kw)
        llms.append(llm)
        return llm

    rc = run(_args(docs=str(tmp_path), run_kind="primary"), **{**fakes, "llm_factory": factory})
    assert rc == 0
    assert len(llms) == 1
    st = _status(tmp_path)
    assert st["result"] == "degraded"  # 실패 소스 1개
    assert st["failed_sources"] == ["한국경제 증권"]
    assert st["coverage_ok"] is True
    assert st["stage1_degraded"] is False
    assert st["article_count"] == 8
    assert st["cost_usd"] == 0.27
    assert st["llm"]["total_cost_usd"] == 0.27
    assert st["run_kind"] == "primary"
    assert st["started_at_kst"] == NOW.isoformat() and st["finished_at_kst"] >= st["started_at_kst"]
    names = {s["name"] for s in st["sources"]}
    assert "더벨 (Google News)" in names and "한국경제 증권" in names
    bell = next(s for s in st["sources"] if s["name"] == "더벨 (Google News)")
    assert bell["required"] is True and bell["category"] == "KR"
    warn = _banners(tmp_path, "banner-warn")
    assert len(warn) == 1 and "한국경제 증권" in warn[0].get_text()
    assert not _banners(tmp_path, "banner-error")
    assert (tmp_path / "reports" / f"{DATE}.html").exists()
    assert (tmp_path / "archive.html").exists()


def test_full_path_success_when_nothing_failed(tmp_path, fakes):
    fakes["collect"] = lambda cfg, **kw: _results(fail_one=False)
    assert run(_args(docs=str(tmp_path)), **fakes) == 0
    st = _status(tmp_path)
    assert st["result"] == "success" and st["failed_sources"] == [] and st["warnings"] == []


def test_required_source_failure_listed(tmp_path, fakes):
    def collect(cfg, **kw):
        out = [r for r in _results(fail_one=False) if not r.name.startswith("더벨")]
        out.append(SourceResult(name="더벨 (Google News)", ok=False, count=0, error="429"))
        return out

    assert run(_args(docs=str(tmp_path)), **{**fakes, "collect": collect}) == 0
    st = _status(tmp_path)
    assert st["result"] == "degraded" and st["failed_sources"] == ["더벨 (Google News)"]
    assert "더벨" in _banners(tmp_path, "banner-warn")[0].get_text()


def test_coverage_missing_category_is_degraded(tmp_path, fakes):
    fakes["collect"] = lambda cfg, **kw: [r for r in _results(fail_one=False) if r.name != "Fed 보도자료"]
    assert run(_args(docs=str(tmp_path)), **fakes) == 0
    st = _status(tmp_path)
    assert st["coverage_ok"] is False and st["result"] == "degraded"
    assert "MACRO" in _banners(tmp_path, "banner-warn")[0].get_text()


def test_stage1_degraded_still_renders(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(run_mod, "run_stage1", _raise(Stage1Degraded("cap")))
    fakes["collect"] = lambda cfg, **kw: _results(fail_one=False)
    assert run(_args(docs=str(tmp_path)), **fakes) == 0
    st = _status(tmp_path)
    assert st["stage1_degraded"] is True and st["result"] == "degraded"
    assert (tmp_path / "reports" / f"{DATE}.html").exists()
    assert "degraded" in _banners(tmp_path, "banner-warn")[0].get_text()


def test_stage2_warnings_and_llm_warnings_degrade(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(
        run_mod, "run_stage2",
        lambda llm, sel, q, d, *, deadline: (_relaxed({**VALID_REPORT, "ai_sector": "짧음"}), ["ai_sector_short"]),
    )

    class CostlyLLM(FakeLLM):
        def warnings(self):
            return ["cost_over_soft_cap"]

    fakes["collect"] = lambda cfg, **kw: _results(fail_one=False)
    assert run(_args(docs=str(tmp_path)), **{**fakes, "llm_factory": CostlyLLM}) == 0
    st = _status(tmp_path)
    assert st["result"] == "degraded"
    assert "ai_sector_short" in st["warnings"] and "cost_over_soft_cap" in st["warnings"]


def test_window_filter_and_max_articles(tmp_path, fakes):
    old = _article(99, minutes_ago=3 * 24 * 60)  # 윈도 밖
    fakes["collect"] = lambda cfg, **kw: _results(fail_one=False) + [
        SourceResult(name="매일경제 증권", ok=True, count=1, articles=[old])]
    assert run(_args(docs=str(tmp_path), max_articles=4), **fakes) == 0
    assert _status(tmp_path)["article_count"] == 4


def test_collect_budget_passed(tmp_path, fakes):
    seen = {}

    def collect(cfg, **kw):
        seen.update(kw)
        return _results()

    assert run(_args(docs=str(tmp_path)), **{**fakes, "collect": collect}) == 0
    assert 0 < seen["deadline_s"] <= run_mod.COLLECT_BUDGET_S


def test_main_exits_zero(tmp_path):
    with pytest.raises(SystemExit) as ei:
        run_mod.main(["--dry-run", "--docs", str(tmp_path), "--date", DATE])
    assert ei.value.code == 0
    assert (tmp_path / "index.html").exists()
