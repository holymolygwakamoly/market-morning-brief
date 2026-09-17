"""소스 접근 프로브(로컬 진단) — 뉴스 소스·Yahoo 지수·구성종목 목록·Yahoo 배치 quote.

각 URL을 GET 하여 HTTP 코드·본문 크기·<item> 건수를 표로 출력한다. LLM 호출 없음. 종료 코드 0.
사용: `.venv\\Scripts\\python scripts\\probe.py [--constituents]`
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_sources() -> dict:
    return yaml.safe_load((ROOT / "brief" / "sources.yaml").read_text(encoding="utf-8"))


def probe(url: str, ua: str, timeout: float) -> tuple[str, int, int, float]:
    t0 = time.perf_counter()
    try:
        r = httpx.get(url, headers={"User-Agent": ua, "Accept": "*/*"}, timeout=timeout, follow_redirects=True)
        body = r.text
        items = len(re.findall(r"<item[\s>]", body)) or len(re.findall(r"<entry[\s>]", body))
        if "chart/" in url and '"result"' in body:
            items = body.count('"close"')
        return str(r.status_code), len(body), items, time.perf_counter() - t0
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, 0, 0, time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--constituents", action="store_true", help="구성종목 목록(Wikipedia/KIND)과 Yahoo 배치 quote까지 확인")
    args = ap.parse_args()
    cfg = load_sources()
    ua = cfg["defaults"]["user_agent"]
    rows: list[tuple[str, str, str, int, int, float]] = []
    for s in cfg["sources"]:
        code, size, items, dt = probe(s["url"], ua, s.get("timeout_s", cfg["defaults"]["timeout_s"]))
        rows.append((s["name"], s["type"], code, size, items, dt))
    from brief.market.yahoo import BROWSER_UA, CHART_URL

    for sym in cfg["market"]["indices"]:
        code, size, items, dt = probe(CHART_URL.format(symbol=sym["symbol"]) + "?range=1mo&interval=1d", BROWSER_UA, 15)
        rows.append((f"Yahoo {sym['symbol']}", "yahoo_chart", code, size, items, dt))

    print(f"{'source':<34} {'type':<12} {'code':<16} {'bytes':>8} {'items':>6} {'sec':>6}  result")
    fails = 0
    for name, typ, code, size, items, dt in rows:
        ok = code == "200" and items > 0
        fails += 0 if ok else 1
        print(f"{name:<34} {typ:<12} {code:<16} {size:>8} {items:>6} {dt:>6.1f}  {'OK' if ok else 'FAIL'}")
    print(f"\n{len(rows) - fails}/{len(rows)} OK")
    thebell = next(r for r in rows if r[0].startswith("더벨"))
    print("THEBELL_GOOGLE_NEWS:", "OK" if thebell[2] == "200" and thebell[4] > 0 else "FAIL")

    if args.constituents:
        from brief.market.constituents import INDEX_SPECS, load_members
        from brief.market.yahoo import YahooClient

        y = YahooClient()
        with httpx.Client(follow_redirects=True) as c:
            for key in cfg["market"]["constituents"]:
                try:
                    members, src = load_members(key, ROOT / cfg["market"].get("cache_dir", ".cache"), client=c)
                    q = y.quotes([m["ticker"] for m in members[:60]])
                    print(f"{key:<12} {INDEX_SPECS[key]['name']:<10} members={len(members):>5} ({src})  yahoo quotes 60→{len(q)}")
                except Exception as e:  # noqa: BLE001
                    print(f"{key:<12} FAIL {type(e).__name__}: {e}")
        y.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
