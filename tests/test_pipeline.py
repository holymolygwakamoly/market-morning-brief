"""v4 파이프라인 E2E(모킹) — update → 스냅샷 파일, regenerate, 토픽 독립 실패, stage1 배치·부분 실패, 핵심 5."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from brief.analyze.client import LLMClient
from brief.analyze.select import TOPICS, select_for_topics
from brief.analyze.stage1_tag import Stage1Degraded, run_stage1
from brief.analyze.topics import top_headlines, run_topics
from brief.collect.base import Article, SourceResult
from brief.market.base import Constituent, ConstituentTable, IndexQuote, MarketSnapshot
from brief.pipeline import load_market, read_status, regenerate, update, write_index

RUN_DATE = date(2026, 9, 17)
NOW = datetime(2026, 9, 17, 7, 30, tzinfo=timezone(timedelta(hours=9)))


# --- 픽스처 ------------------------------------------------------------------------


def make_articles(n: int = 30) -> list[Article]:
    cats = ["US", "KR", "MACRO", "EU", "CN", "GLOBAL"]
    out = []
    for i in range(n):
        c = cats[i % len(cats)]
        out.append(Article(title=f"기사 {i} {c} {'AI 반도체' if i % 5 == 0 else ''}", url=f"https://ex.com/{i}", published_at=NOW - timedelta(hours=1, minutes=i),
                           source=f"src{c}", category=c, lang="ko" if c in ("KR", "MACRO") else "en", summary=f"요약 {i}"))
    out.append(Article(title="더벨 헤드라인 1", url="https://news.google.com/x1", published_at=NOW - timedelta(hours=2), source="더벨 (Google News)", category="KR", lang="ko"))
    return out


def fake_collect(cfg, deadline_s=0):
    arts = make_articles()
    by = {}
    for a in arts:
        by.setdefault(a.source, []).append(a)
    res = [SourceResult(name=k, ok=True, count=len(v), articles=v) for k, v in by.items()]
    # sources.yaml 이름과 맞춰 coverage_ok 계산이 되도록 카테고리 있는 소스 3개를 실제 이름으로 바꾼다
    names = {"srcUS": "CNBC Top News", "srcKR": "연합뉴스 경제", "srcMACRO": "Fed 보도자료", "더벨 (Google News)": "더벨 (Google News)"}
    for r in res:
        r.name = names.get(r.name, r.name)
    res.append(SourceResult(name="MarketWatch Top Stories", ok=False, count=0, error="timeout"))
    return res


def fake_market(cfg, run_date, *, cache_dir, budget_s=0):
    q = lambda sym, name, region, group, key=None, kind="index": IndexQuote(symbol=sym, name=name, region=region, kind=kind, group=group, close=100.0, prev_close=99.0, change_pct=1.01, change_abs=1.0, session_date=run_date - timedelta(days=1), asof=NOW, constituents_key=key)
    rows = [Constituent(ticker=f"T{i}", name=f"Co{i}", sector="Tech", close=10.0 + i, change_pct=float(i - 2), volume=1000, value=10000.0 + i, value_estimated=True, market_cap=1e9 * (i + 1)) for i in range(5)]
    return MarketSnapshot(
        run_date=run_date,
        indices=[q("^GSPC", "S&P 500", "US", "미국", "us_sp500"), q("^KS11", "코스피", "KR", "한국", "kr_kospi"), q("KRW=X", "원/달러", "MACRO", "환율", kind="fx"), q("XLK", "기술", "US", "섹터ETF", kind="etf")],
        constituents={"us_sp500": ConstituentTable(key="us_sp500", index_name="S&P 500", session_date=run_date - timedelta(days=1), currency="USD", rows=rows, total=5, source="test"),
                      "kr_kospi": ConstituentTable(key="kr_kospi", index_name="코스피", session_date=run_date - timedelta(days=1), currency="KRW", rows=rows, total=6, source="test")},
        errors=["^BAD: no data"],
    )


LONG = "가" * 220


def topic_report(title: str) -> dict:
    return {"title": title, "overview": "개요 " + "나" * 160, "sections": [{"heading": f"섹션 {i}", "body": LONG, "bullets": ["포인트"]} for i in range(4)],
            "leaders": [{"name": "Co1", "ticker": "T1", "comment": "코멘트"}], "events_today": ["FOMC"],
            "headlines": [{"text": f"{title} 핵심", "importance": 4}, {"text": f"{title} 둘째", "importance": 2}], "data_caveats": [],
            "disclaimer": "본 보고서는 투자 조언이 아닙니다. 투자 판단과 책임은 투자자 본인에게 있습니다."}


def sector_report() -> dict:
    return {"title": "섹터", "overview": "개요 " + "나" * 160,
            "sectors": [{"name": f"섹터{i}", "direction": "up", "markets": ["US"], "reason": "이유 " + "다" * 110, "news": "뉴스", "leaders": [{"name": "Co1", "ticker": None, "comment": "c"}], "etf_note": None} for i in range(3)],
            "ai_sector": "A" * 320, "cross_events": [], "events_today": [], "headlines": [{"text": "섹터 핵심", "importance": 5}], "data_caveats": [],
            "disclaimer": "본 보고서는 투자 조언이 아닙니다. 투자 판단과 책임은 투자자 본인에게 있습니다."}


class FakeRunner:
    """`claude -p` 대체. argv의 --json-schema title로 스키마를 판별해 유효한 structured_output을 돌려준다."""

    def __init__(self, fail_topics: set[str] | None = None, fail_stage1_offsets: set[int] | None = None) -> None:
        self.fail_topics = fail_topics or set()
        self.fail_stage1_offsets = fail_stage1_offsets or set()
        self.calls: list[str] = []

    def __call__(self, argv, user, env, timeout):
        schema = json.loads(argv[argv.index("--json-schema") + 1])
        system = argv[argv.index("--system-prompt") + 1]
        title = schema.get("title")
        self.calls.append(title)
        if title == "Stage1Result":
            ids = [int(line.split(" | ")[0]) for line in user.splitlines()[1:]]
            if ids and ids[0] in self.fail_stage1_offsets:
                return SimpleNamespace(returncode=1, stdout="", stderr="boom")
            items = [{"i": i, "p": (i % 5) + 1, "m": ["US", "KR", "MACRO", "EU", "CN", "GLOBAL"][i % 6], "a": i % 5 == 0, "s": ["SEMI"], "k": f"story-{i // 2}"} for i in ids]
            out = {"items": items}
        elif title == "SectorReportOut":
            if "sectors" in self.fail_topics:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"is_error": True, "result": "rate limit"}), stderr="")
            out = sector_report()
        else:
            topic = next((t for t in ("거시경제", "미국 주식", "유럽 주식", "국내 주식", "중국·홍콩") if t in system), "?")
            key = {"거시경제": "macro", "미국 주식": "us", "유럽 주식": "eu", "국내 주식": "kr", "중국·홍콩": "cn"}.get(topic, "?")
            if key in self.fail_topics:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"is_error": True, "result": "rate limit"}), stderr="")
            out = topic_report(topic)
        payload = {"structured_output": out, "usage": {"input_tokens": 10, "output_tokens": 500}, "total_cost_usd": 0.1}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")


def llm_factory_with(runner: FakeRunner):
    return lambda sleep=None: LLMClient(runner=runner, claude_bin="python", sleep=sleep or (lambda s: None))


# --- 테스트 ---------------------------------------------------------------------------


def test_update_writes_snapshot_files(tmp_path: Path):
    docs = tmp_path / "docs"
    runner = FakeRunner()
    st = update("2026-09-17", docs, collect=fake_collect, market_fn=fake_market, llm_factory=llm_factory_with(runner), sleep=lambda s: None)
    assert st.result == "degraded"  # MarketWatch 실패 소스 포함
    assert st.failed_sources == ["MarketWatch Top Stories"] and st.coverage_ok
    assert all(v["ok"] for v in st.topics.values()) and set(st.topics) == set(TOPICS)
    base = docs / "data" / "2026-09-17"
    for f in ("home.json", "status.json", "inputs.json", "indices/us_sp500.json", "indices/kr_kospi.json", *[f"reports/{t}.json" for t in TOPICS]):
        assert (base / f).exists(), f
    home = json.loads((base / "home.json").read_text(encoding="utf-8"))
    assert len(home["indices"]) == 4 and home["constituents"]["us_sp500"]["rows"] == 5
    assert len(home["headlines"]) == 5 and home["headlines"][0]["importance"] == 5  # 섹터 핵심(5)이 최상위
    assert home["topics"]["us"]["ok"]
    idx = json.loads((docs / "data" / "index.json").read_text(encoding="utf-8"))
    assert idx["dates"][0]["date"] == "2026-09-17" and idx["dates"][0]["topics_ok"] == 6
    assert (docs / "index.html").exists() and (docs / ".nojekyll").exists()
    us = json.loads((base / "reports" / "us.json").read_text(encoding="utf-8"))
    assert us["ok"] and us["report"]["sections"][0]["body"].startswith("가") and us["articles"]
    kr = json.loads((base / "reports" / "kr.json").read_text(encoding="utf-8"))
    assert kr["thebell"] and kr["thebell"][0]["title"] == "더벨 헤드라인 1"
    # 호출 수: stage1 1배치 + 토픽 6
    assert runner.calls.count("Stage1Result") == 1 and len(runner.calls) == 7
    assert st.llm["total_calls"] == 7 and st.llm["billed"] is False


def test_topic_failure_is_isolated_and_regenerate_fixes_it(tmp_path: Path):
    docs = tmp_path / "docs"
    runner = FakeRunner(fail_topics={"eu"})
    st = update("2026-09-17", docs, collect=fake_collect, market_fn=fake_market, llm_factory=llm_factory_with(runner), sleep=lambda s: None)
    assert st.result == "degraded" and not st.topics["eu"]["ok"] and st.topics["us"]["ok"]
    assert st.topics["eu"]["attempts"] == 2  # TOPIC_MAX_CALLS
    eu = json.loads((docs / "data" / "2026-09-17" / "reports" / "eu.json").read_text(encoding="utf-8"))
    assert not eu["ok"] and "rate limit" in eu["error"]
    home = json.loads((docs / "data" / "2026-09-17" / "home.json").read_text(encoding="utf-8"))
    assert not home["topics"]["eu"]["ok"]

    # 재생성: 저장된 inputs + 시장 데이터로 eu만
    runner2 = FakeRunner()
    st2 = regenerate("2026-09-17", ("eu",), docs, llm_factory=llm_factory_with(runner2), sleep=lambda s: None)
    assert runner2.calls == ["TopicReportOut"]
    assert st2.run_kind == "regenerate" and st2.topics["eu"]["ok"] and st2.topics["us"]["ok"]
    assert st2.result == "degraded"  # 실패 소스는 그대로
    eu2 = json.loads((docs / "data" / "2026-09-17" / "reports" / "eu.json").read_text(encoding="utf-8"))
    assert eu2["ok"] and eu2["report"]["title"] == "유럽 주식"
    home2 = json.loads((docs / "data" / "2026-09-17" / "home.json").read_text(encoding="utf-8"))
    assert home2["topics"]["eu"]["ok"] and any(h["topic"] == "eu" for h in home2["headlines"])
    market = load_market(docs, "2026-09-17")
    assert len(market.indices) == 4 and set(market.constituents) == {"us_sp500", "kr_kospi"}


def test_update_never_raises_and_records_failed_status(tmp_path: Path):
    docs = tmp_path / "docs"

    def boom(cfg, deadline_s=0):
        raise RuntimeError("collect exploded")

    st = update("2026-09-17", docs, collect=boom, market_fn=fake_market, llm_factory=llm_factory_with(FakeRunner()), sleep=lambda s: None)
    assert st.result == "failed" and "collect exploded" in st.error
    assert read_status(docs, "2026-09-17")["result"] == "failed"
    assert all(not v["ok"] for v in st.topics.values())
    assert (docs / "data" / "2026-09-17" / "home.json").exists()


def test_stage1_batches_parallel_and_partial_failure():
    arts = make_articles(30)[:30]
    llm = LLMClient(runner=FakeRunner(fail_stage1_offsets={10}), claude_bin="python", sleep=lambda s: None)
    tags = run_stage1(llm, arts, deadline=1e12, batch_size=10, max_parallel=2)
    assert tags.partial and {it.i for it in tags.items} == set(range(0, 10)) | set(range(20, 30))
    assert llm.calls["stage1"] == 1 + 2 + 1  # 배치 3개, 실패 배치는 캡(2)까지 재시도
    llm2 = LLMClient(runner=FakeRunner(fail_stage1_offsets={0, 10, 20}), claude_bin="python", sleep=lambda s: None)
    with pytest.raises(Stage1Degraded):
        run_stage1(llm2, arts, deadline=1e12, batch_size=10)


def test_select_for_topics_quotas_and_degrade():
    arts = make_articles(60)
    tags = run_stage1(LLMClient(runner=FakeRunner(), claude_bin="python", sleep=lambda s: None), arts, deadline=1e12)
    sel = select_for_topics(arts, tags)
    assert set(sel) == set(TOPICS)
    assert all(a.category == "EU" or a.category in ("MACRO", "GLOBAL") for a in sel["eu"].articles)
    assert sel["kr"].thebell and not sel["us"].thebell and sel["sectors"].thebell
    assert "더벨" not in " ".join(a.source for a in sel["kr"].articles)
    deg = select_for_topics(arts, None)
    assert deg["us"].degraded and all(a.category in ("US", "MACRO", "GLOBAL") or "AI" in a.title for a in deg["us"].articles)
    assert deg["sectors"].tags is None


def test_top_headlines_dedupes_and_limits():
    runner = FakeRunner()
    llm = LLMClient(runner=runner, claude_bin="python", sleep=lambda s: None)
    arts = make_articles(12)
    sel = select_for_topics(arts, None)
    res = run_topics(llm, sel, fake_market({}, RUN_DATE, cache_dir=Path(".")), RUN_DATE, deadline=1e12, max_parallel=3)
    hs = top_headlines(res)
    assert len(hs) == 5 and hs[0]["topic"] == "sectors" and len({h["text"] for h in hs}) == 5


def test_write_index_lists_dates_desc(tmp_path: Path):
    docs = tmp_path / "docs"
    for d, r in (("2026-09-15", "success"), ("2026-09-17", "degraded")):
        p = docs / "data" / d / "status.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"result": r, "finished_at_kst": f"{d}T09:00:00+09:00", "topics": {"us": {"ok": True}}}), encoding="utf-8")
    dates = write_index(docs)
    assert [d["date"] for d in dates] == ["2026-09-17", "2026-09-15"] and dates[0]["topics_ok"] == 1
