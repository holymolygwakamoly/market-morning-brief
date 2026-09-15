"""오케스트레이션 — `python -m brief.run [--date] [--dry-run] [--skip-if-done] [--run-kind] ...`.

순서: 시작 시각(KST) → --skip-if-done 검사 → collect → quotes → window → dedupe → stage1(실패 시 degrade)
→ select → stage2 → render(status 포함). 최상위 `except BaseException`으로 어떤 실패든 에러 배너 + status
`failed` 를 남기고 **항상 exit 0** (커밋은 워크플로가 `if: always()`로 진행).

`--dry-run`: LLM·네트워크 0회. `brief/fixtures/*_dry_run.json`의 기사/시세/보고서를 사용하며, 픽스처의
published_at이 고정값이므로 **윈도 필터(filter_window)를 건너뛴다**(어느 날짜로 실행해도 동일 결과).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
from collections.abc import Callable
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from brief.analyze.client import CallCapExceeded, LLMClient, SkippedForDeadline
from brief.analyze.schemas import Report
from brief.analyze.select import select_for_stage2
from brief.analyze.stage1_tag import Stage1Degraded, run_stage1
from brief.analyze.stage2_write import run_stage2
from brief.collect import articles_from, collect_all
from brief.collect.base import Article, Quote, SourceResult
from brief.collect.yahoo_chart import fetch_quotes
from brief.config import KST, MAX_ARTICLES, load_sources
from brief.dedupe import dedupe
from brief.render.context import RunStatus
from brief.render.fallback import write_error_banner
from brief.render.render import render_report
from brief.window import filter_window

logger = logging.getLogger(__name__)

INTERNAL_DEADLINE_S = 14 * 60
COLLECT_BUDGET_S = 180
# 단계 진입 시 요구하는 최소 잔여 시간(초). 부족하면 TimeoutError("deadline").
MIN_REMAINING = {"collect": 30, "stage1": 60, "stage2": 240, "render": 5}
# collect 이후 단계(LLM·렌더)를 위해 남겨 둘 시간.
POST_COLLECT_RESERVE_S = 300

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CATEGORIES = ("US", "KR", "MACRO")


# --- CLI ----------------------------------------------------------------------


def parse_args(argv: list[str] | None = None, *, now: datetime | None = None) -> argparse.Namespace:
    today = (now or datetime.now(KST)).astimezone(KST).date().isoformat()
    p = argparse.ArgumentParser(prog="brief.run", description="Market Morning Brief 생성기")
    p.add_argument("--date", default=today, help=f"보고서 날짜 YYYY-MM-DD (기본: 오늘 KST = {today})")
    p.add_argument("--dry-run", action="store_true", help="LLM·네트워크 없이 패키지 픽스처로 렌더")
    p.add_argument("--max-articles", type=int, default=None, help=f"MAX_ARTICLES 재정의 (기본 {MAX_ARTICLES})")
    p.add_argument("--skip-if-done", action="store_true", help="당일 status가 success/degraded면 즉시 종료")
    p.add_argument("--run-kind", choices=("primary", "backup", "manual"), default="manual")
    p.add_argument("--docs", default="docs", help="출력 디렉터리 (기본 docs)")
    p.add_argument("--sources", default=None, help="sources.yaml 경로 (기본 brief/sources.yaml)")
    return p.parse_args(argv)


def is_done(docs_dir: Path, date: str) -> bool:
    """docs/status/{date}.json 의 result가 success/degraded면 True."""
    p = Path(docs_dir) / "status" / f"{date}.json"
    if not p.exists():
        return False
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("result") in ("success", "degraded")
    except (ValueError, OSError):
        return False


# --- helpers ------------------------------------------------------------------


def _load_fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _dry_run_sources(articles: list[Article]) -> list[SourceResult]:
    by_source: dict[str, list[Article]] = {}
    for a in articles:
        by_source.setdefault(a.source, []).append(a)
    return [
        SourceResult(name=name, ok=True, count=len(arts), articles=arts) for name, arts in by_source.items()
    ]


def _apply_source_status(status: RunStatus, results: list[SourceResult], cfg: dict) -> None:
    meta = {s["name"]: s for s in cfg.get("sources", [])}
    status.sources = []
    status.failed_sources = []
    covered: set[str] = set()
    for r in results:
        m = meta.get(r.name, {})
        category = m.get("category")
        status.sources.append(
            {
                "name": r.name,
                "ok": r.ok,
                "count": r.count,
                "error": r.error,
                "required": bool(m.get("required", False)),
                "category": category,
                "elapsed_s": round(r.elapsed_s, 2),
            }
        )
        if r.ok and r.count > 0 and category:
            covered.add(category)
        if not r.ok:
            status.failed_sources.append(r.name)
    # cfg에 있으나 결과에 없는 required 소스(어댑터 생성 실패 등)도 누락으로 기록
    seen = {r.name for r in results}
    for name, m in meta.items():
        if m.get("required") and name not in seen:
            status.failed_sources.append(name)
    status.coverage_ok = all(c in covered for c in CATEGORIES)


# --- run ----------------------------------------------------------------------


def run(
    args: argparse.Namespace,
    *,
    now: datetime | None = None,
    collect: Callable[..., list[SourceResult]] = collect_all,
    quotes_fn: Callable[..., tuple[list[Quote], list[str]]] = fetch_quotes,
    llm_factory: Callable[..., Any] = LLMClient,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    t0 = time.monotonic()
    started = (now or datetime.now(KST)).astimezone(KST)

    def clock() -> datetime:
        return started + timedelta(seconds=time.monotonic() - t0) if now is not None else datetime.now(KST)

    date: str = args.date or started.date().isoformat()
    run_date = _date.fromisoformat(date)
    docs = Path(args.docs)
    status = RunStatus(date=date, run_kind=args.run_kind, started_at_kst=started.isoformat())

    if args.skip_if_done and is_done(docs, date):
        logger.info("already done: %s/status/%s.json is success/degraded -> skip", docs, date)
        return 0

    deadline = t0 + INTERNAL_DEADLINE_S

    def remaining() -> float:
        return deadline - time.monotonic()

    def require(stage: str) -> None:
        if remaining() < MIN_REMAINING[stage]:
            raise TimeoutError(f"deadline: {stage} 진입 시 잔여 {remaining():.0f}s < {MIN_REMAINING[stage]}s")

    try:
        cfg = load_sources(args.sources)
        max_articles = args.max_articles or MAX_ARTICLES

        # 1) collect + quotes
        require("collect")
        logger.info("stage: collect")
        if args.dry_run:
            articles = [Article(**x) for x in _load_fixture("articles_dry_run.json")]
            results = _dry_run_sources(articles)
            quotes = [Quote(**x) for x in _load_fixture("quotes_dry_run.json")]
            quote_errors: list[str] = []
        else:
            budget = min(COLLECT_BUDGET_S, max(MIN_REMAINING["collect"], remaining() - POST_COLLECT_RESERVE_S))
            results = collect(cfg, deadline_s=budget)
            quotes, quote_errors = quotes_fn(cfg.get("quotes", {}), cfg.get("defaults", {}))
            articles = filter_window(articles_from(results), run_date)
        _apply_source_status(status, results, cfg)
        logger.info(
            "collect: %d sources (%d failed), coverage_ok=%s, quotes=%d (%d errors)",
            len(results), len(status.failed_sources), status.coverage_ok, len(quotes), len(quote_errors),
        )
        articles = dedupe(articles, max_total=max_articles)
        status.article_count = len(articles)
        logger.info("articles after window+dedupe: %d", len(articles))

        # 2) stage1 / select / stage2
        llm = None
        stage1 = None
        warnings: list[str] = []
        if args.dry_run:
            report = Report(**_load_fixture("report_dry_run.json"))
            selected = select_for_stage2(articles, None)
            selected.degraded = False
        else:
            llm = llm_factory(sleep=sleep)
            require("stage1")
            logger.info("stage: stage1")
            try:
                stage1 = run_stage1(llm, articles, deadline=deadline)
            except Stage1Degraded as e:
                logger.warning("stage1 degraded: %s", e)
                status.stage1_degraded = True
            selected = select_for_stage2(articles, stage1)
            require("stage2")
            logger.info("stage: stage2")
            try:
                report, warnings = run_stage2(llm, selected, quotes, run_date, deadline=deadline)
            except (CallCapExceeded, SkippedForDeadline) as e:
                raise RuntimeError(f"stage2_failed: {e}") from e

        # 3) status + render
        if llm is not None:
            status.llm = llm.summary()
            status.cost_usd = round(llm.total_cost_usd(), 6)
            warnings = list(warnings) + list(llm.warnings())
        for w in warnings:
            if w not in status.warnings:
                status.warnings.append(w)
        require("render")
        logger.info("stage: render")
        status.result = (
            "degraded"
            if (status.failed_sources or status.stage1_degraded or status.warnings or not status.coverage_ok)
            else "success"
        )
        status.finished_at_kst = clock().isoformat()
        out = render_report(
            report,
            status=status,
            quotes=quotes,
            quote_errors=quote_errors,
            selected=selected,
            docs_dir=docs,
            date=date,
        )
        logger.info(
            "done: result=%s articles=%d selected=%d cost_usd=%.4f warnings=%s -> %s",
            status.result, status.article_count, len(selected.articles), status.cost_usd, status.warnings, out,
        )
        return 0
    except BaseException as e:  # noqa: BLE001 — KeyboardInterrupt/SystemExit 포함, 항상 exit 0
        reason = f"{type(e).__name__}: {e}"[:300]
        logger.error("run failed: %s\n%s", reason, traceback.format_exc())
        status.result = "failed"
        status.error = reason
        try:
            write_error_banner(docs, reason=reason, now_kst=clock(), date=date, status=status)
        except BaseException as e2:  # noqa: BLE001
            logger.error("write_error_banner failed: %r", e2)
        return 0


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run(parse_args(argv)))


if __name__ == "__main__":
    main()
