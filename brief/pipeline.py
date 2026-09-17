"""v4 파이프라인 — `update(date)`: market ∥ collect → window/dedupe → stage1 → select → 토픽 6종 → 스냅샷 JSON.

출력(`docs/data/<date>/`): home.json · indices/<key>.json · reports/<topic>.json · inputs.json(재생성용) · status.json,
그리고 `docs/data/index.json`(기준일 목록). 페이지 자체는 정적 SPA(`docs/index.html`)가 이 JSON을 읽는다.
`regenerate(date, topics)`: 저장된 inputs.json + 시장 데이터로 해당 토픽의 stage2만 다시 실행한다.
어떤 예외든 status.json에 `result: failed` + error 를 남기고 정상 반환한다(호출자는 status로 판정).
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from datetime import datetime
from pathlib import Path
from typing import Any

from brief.analyze.client import LLMClient
from brief.analyze.schemas import Stage1Item, Stage1Tags
from brief.analyze.select import TOPIC_NAMES, TOPICS, Selected, select_for_topics
from brief.analyze.stage1_tag import Stage1Degraded, run_stage1
from brief.analyze.topics import TopicResult, run_topics, top_headlines
from brief.collect import articles_from, collect_all
from brief.collect.base import Article, SourceResult
from brief.config import KST, MAX_ARTICLES, load_sources
from brief.dedupe import dedupe
from brief.market import collect_market
from brief.market.base import ConstituentTable, IndexQuote, MarketSnapshot
from brief.window import filter_window

logger = logging.getLogger(__name__)

INTERNAL_DEADLINE_S = 45 * 60
COLLECT_BUDGET_S = 180
MARKET_BUDGET_S = 300
MAX_ARTICLES_V4 = 400
MIN_REMAINING = {"collect": 30, "stage1": 90, "stage2": 200, "render": 5}
COVERAGE_CATEGORIES = ("US", "KR", "MACRO")
TEMPLATE_HTML = Path(__file__).resolve().parent / "render" / "templates" / "research.html"


# --- 상태 ---------------------------------------------------------------------


@dataclass
class UpdateStatus:
    date: str
    run_kind: str = "update"  # update | regenerate
    started_at_kst: str = ""
    finished_at_kst: str = ""
    result: str = "failed"  # success | degraded | failed
    sources: list[dict] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    coverage_ok: bool = False
    article_count: int = 0
    stage1_degraded: bool = False
    stage1_partial: bool = False
    market: dict = field(default_factory=dict)
    topics: dict[str, dict] = field(default_factory=dict)
    llm: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed_s: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def read_status(docs: Path, date: str) -> dict:
    p = Path(docs) / "data" / date / "status.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def is_done(docs: Path, date: str) -> bool:
    return read_status(docs, date).get("result") in ("success", "degraded")


# --- 저장 ---------------------------------------------------------------------


def _write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(p)


def write_index(docs: Path) -> list[dict]:
    """docs/data/*/status.json 을 훑어 기준일 목록(최신순)을 index.json 에 쓴다."""
    data_dir = Path(docs) / "data"
    dates: list[dict] = []
    if data_dir.exists():
        for d in sorted((x for x in data_dir.iterdir() if x.is_dir() and len(x.name) == 10), key=lambda x: x.name, reverse=True):
            st = read_status(docs, d.name)
            if not st:
                continue
            topics_ok = sum(1 for t in (st.get("topics") or {}).values() if t.get("ok"))
            dates.append({"date": d.name, "result": st.get("result"), "updated_at_kst": st.get("finished_at_kst"), "topics_ok": topics_ok})
    _write_json(data_dir / "index.json", {"dates": dates, "generated_at_kst": datetime.now(KST).isoformat()})
    return dates


def ensure_spa(docs: Path) -> None:
    """정적 SPA(index.html)를 템플릿에서 복사한다(항상 최신 템플릿으로 덮어씀)."""
    docs = Path(docs)
    docs.mkdir(parents=True, exist_ok=True)
    if TEMPLATE_HTML.exists():
        shutil.copyfile(TEMPLATE_HTML, docs / "index.html")
    (docs / ".nojekyll").touch(exist_ok=True)


def _article_brief(a: Article) -> dict:
    return {"title": a.title, "url": a.url, "source": a.source, "publisher": a.publisher, "lang": a.lang, "category": a.category,
            "published_at_kst": a.published_at.astimezone(KST).strftime("%Y-%m-%d %H:%M")}


def write_report(docs: Path, date: str, res: TopicResult, selected: Selected | None) -> None:
    obj = {
        "topic": res.topic,
        "name": TOPIC_NAMES.get(res.topic, res.topic),
        "ok": res.ok,
        "error": res.error,
        "warnings": res.warnings,
        "attempts": res.attempts,
        "generated_at_kst": datetime.now(KST).isoformat(),
        "report": res.report.model_dump() if res.report else None,
        "articles": [_article_brief(a) for a in (selected.articles if selected else [])],
        "thebell": [_article_brief(a) for a in (selected.thebell if selected else [])],
        "degraded_selection": bool(selected.degraded) if selected else False,
    }
    _write_json(Path(docs) / "data" / date / "reports" / f"{res.topic}.json", obj)


def write_market(docs: Path, date: str, market: MarketSnapshot) -> dict:
    base = Path(docs) / "data" / date
    summary: dict[str, dict] = {}
    for key, t in market.constituents.items():
        _write_json(base / "indices" / f"{key}.json", t.model_dump(mode="json"))
        summary[key] = {"index_name": t.index_name, "rows": len(t.rows), "total": t.total, "session_date": t.session_date,
                        "note": t.note, "error": t.error, "source": t.source}
    return summary


def write_home(docs: Path, date: str, *, market: MarketSnapshot, constituents: dict, results: dict[str, TopicResult], status: UpdateStatus) -> None:
    obj = {
        "date": date,
        "updated_at_kst": status.finished_at_kst or datetime.now(KST).isoformat(),
        "result": status.result,
        "indices": [q.model_dump(mode="json") for q in market.indices],
        "constituents": constituents,
        "headlines": top_headlines(results),
        "topics": {t: {"name": TOPIC_NAMES.get(t, t), "ok": r.ok, "error": r.error, "warnings": r.warnings,
                       "title": getattr(r.report, "title", None) if r.report else None} for t, r in results.items()},
        "sources": {"ok": sum(1 for s in status.sources if s.get("ok")), "total": len(status.sources), "failed": status.failed_sources},
        "warnings": status.warnings,
        "market_errors": market.errors,
    }
    _write_json(Path(docs) / "data" / date / "home.json", obj)


def write_inputs(docs: Path, date: str, articles: list[Article], stage1: Stage1Tags | None) -> None:
    obj = {
        "articles": [a.model_dump(mode="json") for a in articles],
        "stage1": {"items": [it.model_dump() for it in stage1.items], "partial": stage1.partial} if stage1 else None,
    }
    _write_json(Path(docs) / "data" / date / "inputs.json", obj)


def load_inputs(docs: Path, date: str) -> tuple[list[Article], Stage1Tags | None]:
    p = Path(docs) / "data" / date / "inputs.json"
    obj = json.loads(p.read_text(encoding="utf-8"))
    articles = [Article(**a) for a in obj["articles"]]
    st = obj.get("stage1")
    stage1 = Stage1Tags(items=[Stage1Item(**it) for it in st["items"]], partial=bool(st.get("partial"))) if st else None
    return articles, stage1


def load_market(docs: Path, date: str) -> MarketSnapshot:
    base = Path(docs) / "data" / date
    home = json.loads((base / "home.json").read_text(encoding="utf-8"))
    snap = MarketSnapshot(run_date=_date.fromisoformat(date), indices=[IndexQuote(**q) for q in home.get("indices", [])],
                          errors=list(home.get("market_errors") or []))
    idx_dir = base / "indices"
    if idx_dir.exists():
        for f in idx_dir.glob("*.json"):
            snap.constituents[f.stem] = ConstituentTable(**json.loads(f.read_text(encoding="utf-8")))
    return snap


# --- 소스 상태 ----------------------------------------------------------------


def _apply_source_status(status: UpdateStatus, results: list[SourceResult], cfg: dict) -> None:
    meta = {s["name"]: s for s in cfg.get("sources", [])}
    status.sources, status.failed_sources = [], []
    covered: set[str] = set()
    for r in results:
        m = meta.get(r.name, {})
        category = m.get("category")
        status.sources.append({"name": r.name, "ok": r.ok, "count": r.count, "error": r.error,
                               "required": bool(m.get("required", False)), "category": category, "elapsed_s": round(r.elapsed_s, 2)})
        if r.ok and r.count > 0 and category:
            covered.add(category)
        if not r.ok:
            status.failed_sources.append(r.name)
    seen = {r.name for r in results}
    for name, m in meta.items():
        if m.get("required") and name not in seen:
            status.failed_sources.append(name)
    status.coverage_ok = all(c in covered for c in COVERAGE_CATEGORIES)


def _finalize(status: UpdateStatus, results: dict[str, TopicResult], llm: LLMClient | None, t0: float) -> None:
    for t, r in results.items():
        status.topics[t] = {"ok": r.ok, "error": r.error, "warnings": r.warnings, "attempts": r.attempts}
    if llm is not None:
        status.llm = llm.summary()
    n_ok = sum(1 for r in results.values() if r.ok)
    if n_ok == 0:
        status.result = "failed"
        status.error = status.error or "모든 보고서 생성 실패: " + "; ".join(f"{t}: {r.error}" for t, r in results.items() if r.error)[:400]
    elif n_ok < len(results) or status.failed_sources or status.stage1_degraded or status.stage1_partial or not status.coverage_ok or status.warnings:
        status.result = "degraded"
    else:
        status.result = "success"
    status.finished_at_kst = datetime.now(KST).isoformat()
    status.elapsed_s = round(time.monotonic() - t0, 1)


# --- update -------------------------------------------------------------------


def update(
    date: str,
    docs: Path | str = "docs",
    *,
    sources_path: str | None = None,
    collect: Callable[..., list[SourceResult]] = collect_all,
    market_fn: Callable[..., MarketSnapshot] = collect_market,
    llm_factory: Callable[..., Any] = LLMClient,
    topics: tuple[str, ...] = TOPICS,
    max_articles: int = MAX_ARTICLES_V4,
    sleep: Callable[[float], None] = time.sleep,
) -> UpdateStatus:
    t0 = time.monotonic()
    docs = Path(docs)
    run_date = _date.fromisoformat(date)
    status = UpdateStatus(date=date, run_kind="update", started_at_kst=datetime.now(KST).isoformat())
    deadline = t0 + INTERNAL_DEADLINE_S
    results: dict[str, TopicResult] = {t: TopicResult(topic=t, error="not started") for t in topics}
    llm = None
    market = MarketSnapshot(run_date=run_date)
    selected: dict[str, Selected] = {}

    def remaining() -> float:
        return deadline - time.monotonic()

    def require(stage: str) -> None:
        if remaining() < MIN_REMAINING[stage]:
            raise TimeoutError(f"deadline: {stage} 진입 시 잔여 {remaining():.0f}s")

    try:
        cfg = load_sources(sources_path)
        market_cfg = cfg.get("market") or {}
        cache_dir = Path(market_cfg.get("cache_dir") or ".cache")

        # 1) market ∥ collect
        require("collect")
        logger.info("stage: collect")
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_market = ex.submit(market_fn, market_cfg, run_date, cache_dir=cache_dir, budget_s=MARKET_BUDGET_S)
            f_collect = ex.submit(collect, cfg, deadline_s=COLLECT_BUDGET_S)
            results_src = f_collect.result()
            try:
                market = f_market.result()
            except Exception as e:  # noqa: BLE001 — 시장 데이터 실패는 비치명
                logger.error("market failed: %s", e)
                market = MarketSnapshot(run_date=run_date, errors=[f"market: {e}"])
        _apply_source_status(status, results_src, cfg)
        articles = dedupe(filter_window(articles_from(results_src), run_date), max_total=max(max_articles, MAX_ARTICLES))
        status.article_count = len(articles)
        status.market = {"indices": len(market.indices), "errors": market.errors, "elapsed_s": market.elapsed_s,
                         "constituents": {k: [len(t.rows), t.total] for k, t in market.constituents.items()}}
        logger.info("collect: %d sources (%d failed), articles=%d, indices=%d", len(results_src), len(status.failed_sources), len(articles), len(market.indices))

        # 2) stage1
        llm = llm_factory(sleep=sleep)
        stage1: Stage1Tags | None = None
        require("stage1")
        logger.info("stage: stage1")
        try:
            stage1 = run_stage1(llm, articles, deadline=deadline)
            status.stage1_partial = stage1.partial
        except Stage1Degraded as e:
            logger.warning("stage1 degraded: %s", e)
            status.stage1_degraded = True
        selected = select_for_topics(articles, stage1, topics=topics)
        write_inputs(docs, date, articles, stage1)

        # 3) stage2 토픽 6종(병렬)
        require("stage2")
        logger.info("stage: stage2")
        results = run_topics(llm, selected, market, run_date, deadline=deadline, topics=topics)
        for t, r in results.items():
            status.warnings.extend(f"{t}:{w}" for w in r.warnings)
    except BaseException as e:  # noqa: BLE001 — 어떤 실패든 status로 기록
        status.error = f"{type(e).__name__}: {e}"[:300]
        logger.error("update failed: %s\n%s", status.error, traceback.format_exc())
    finally:
        try:
            require("render")
        except TimeoutError:
            pass
        logger.info("stage: render")
        _finalize(status, results, llm, t0)
        for t, r in results.items():
            write_report(docs, date, r, selected.get(t))
        write_home(docs, date, market=market, constituents=write_market(docs, date, market), results=results, status=status)
        _write_json(docs / "data" / date / "status.json", json.loads(status.to_json()))
        ensure_spa(docs)
        write_index(docs)
        logger.info("done: result=%s topics_ok=%d/%d elapsed=%.0fs", status.result, sum(1 for r in results.values() if r.ok), len(results), status.elapsed_s)
    return status


# --- regenerate ---------------------------------------------------------------


def regenerate(
    date: str,
    topics: tuple[str, ...],
    docs: Path | str = "docs",
    *,
    llm_factory: Callable[..., Any] = LLMClient,
    sleep: Callable[[float], None] = time.sleep,
) -> UpdateStatus:
    """저장된 inputs.json·시장 데이터로 지정 토픽의 stage2만 다시 실행하고 status.json 을 갱신한다."""
    t0 = time.monotonic()
    docs = Path(docs)
    prev = read_status(docs, date)
    status = UpdateStatus(**{k: v for k, v in prev.items() if k in UpdateStatus.__dataclass_fields__}) if prev else UpdateStatus(date=date)
    status.run_kind = "regenerate"
    status.started_at_kst = datetime.now(KST).isoformat()
    status.error = None
    deadline = t0 + INTERNAL_DEADLINE_S
    results: dict[str, TopicResult] = {t: TopicResult(topic=t, error="not started") for t in topics}
    llm = None
    try:
        articles, stage1 = load_inputs(docs, date)
        market = load_market(docs, date)
        selected = select_for_topics(articles, stage1, topics=topics)
        llm = llm_factory(sleep=sleep)
        logger.info("stage: stage2 (regenerate %s)", ",".join(topics))
        results = run_topics(llm, selected, market, _date.fromisoformat(date), deadline=deadline, topics=topics)
        for t, r in results.items():
            write_report(docs, date, r, selected.get(t))
            status.topics[t] = {"ok": r.ok, "error": r.error, "warnings": r.warnings, "attempts": r.attempts}
            status.warnings = [w for w in status.warnings if not w.startswith(f"{t}:")] + [f"{t}:{w}" for w in r.warnings]
        # 전체 결과 재판정(다른 토픽 status 유지)
        if llm is not None:
            prev_llm = status.llm or {}
            status.llm = {"engine": "claude-cli", "billed": False, "regenerate": llm.summary(), "update": prev_llm.get("update") or prev_llm}
        n_ok = sum(1 for v in status.topics.values() if v.get("ok"))
        status.result = "failed" if n_ok == 0 else ("success" if n_ok == len(status.topics) and not status.failed_sources and not status.warnings else "degraded")
        # home.json 갱신(headlines·topics)
        home_p = docs / "data" / date / "home.json"
        home = json.loads(home_p.read_text(encoding="utf-8"))
        all_results: dict[str, TopicResult] = {}
        for t in home.get("topics", {}):
            rp = docs / "data" / date / "reports" / f"{t}.json"
            if rp.exists():
                rj = json.loads(rp.read_text(encoding="utf-8"))
                rep = None
                if rj.get("report"):
                    from brief.analyze.topics import TOPIC_SCHEMAS  # noqa: PLC0415

                    rep = TOPIC_SCHEMAS[t].model_validate(rj["report"])
                all_results[t] = TopicResult(topic=t, report=rep, error=rj.get("error"), warnings=rj.get("warnings") or [])
        home["headlines"] = top_headlines(all_results)
        home["topics"] = {t: {"name": TOPIC_NAMES.get(t, t), "ok": r.ok, "error": r.error, "warnings": r.warnings,
                              "title": getattr(r.report, "title", None) if r.report else None} for t, r in all_results.items()}
        home["result"] = status.result
        home["updated_at_kst"] = datetime.now(KST).isoformat()
        _write_json(home_p, home)
    except BaseException as e:  # noqa: BLE001
        status.error = f"{type(e).__name__}: {e}"[:300]
        logger.error("regenerate failed: %s\n%s", status.error, traceback.format_exc())
        for t in topics:
            status.topics[t] = {"ok": False, "error": status.error, "warnings": [], "attempts": 0}
    finally:
        status.finished_at_kst = datetime.now(KST).isoformat()
        status.elapsed_s = round(time.monotonic() - t0, 1)
        _write_json(docs / "data" / date / "status.json", json.loads(status.to_json()))
        write_index(docs)
    return status


# --- CLI ----------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    today = datetime.now(KST).date().isoformat()
    p = argparse.ArgumentParser(prog="brief.pipeline", description="리서치 대시보드 업데이트")
    p.add_argument("--date", default=today, help=f"기준일 YYYY-MM-DD (기본 오늘 KST = {today})")
    p.add_argument("--docs", default="docs")
    p.add_argument("--regenerate", default=None, help="쉼표로 구분한 토픽만 재생성 (예: us,kr)")
    p.add_argument("--topics", default=None, help="업데이트 시 생성할 토픽 제한(쉼표)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    if args.regenerate:
        st = regenerate(args.date, tuple(x.strip() for x in args.regenerate.split(",") if x.strip()), args.docs)
    else:
        topics = tuple(x.strip() for x in args.topics.split(",")) if args.topics else TOPICS
        st = update(args.date, args.docs, topics=topics)
    print(f"result={st.result} error={st.error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
