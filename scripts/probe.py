"""소스 접근 프로브 — 로컬과 GitHub 러너에서 동일하게 실행.

각 소스 URL을 GET 하여 HTTP 코드·본문 크기·<item> 건수를 표로 출력한다.
LLM 호출 없음. 실패해도 종료 코드는 0(워크플로 job을 실패시키지 않음)."""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_sources() -> dict:
    return yaml.safe_load((ROOT / "brief" / "sources.yaml").read_text(encoding="utf-8"))


def probe(url: str, ua: str, timeout: float) -> tuple[str, int, int, float]:
    t0 = time.perf_counter()
    try:
        r = httpx.get(url, headers={"User-Agent": ua, "Accept": "*/*"}, timeout=timeout, follow_redirects=True)
        body = r.text
        items = len(re.findall(r"<item[\s>]", body)) or len(re.findall(r"<entry[\s>]", body))
        if "chart/" in url and '"result"' in body:
            items = 1
        return str(r.status_code), len(body), items, time.perf_counter() - t0
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, 0, 0, time.perf_counter() - t0


def main() -> int:
    cfg = load_sources()
    ua = cfg["defaults"]["user_agent"]
    rows: list[tuple[str, str, str, int, int, float]] = []
    for s in cfg["sources"]:
        code, size, items, dt = probe(s["url"], ua, s.get("timeout_s", cfg["defaults"]["timeout_s"]))
        rows.append((s["name"], s["type"], code, size, items, dt))
    q = cfg["quotes"]
    for sym in q["symbols"]:
        url = q["url_template"].format(symbol=sym["symbol"])
        code, size, items, dt = probe(url, ua, q["timeout_s"])
        rows.append((f"Yahoo {sym['symbol']}", "yahoo_chart", code, size, items, dt))

    print(f"{'source':<28} {'type':<12} {'code':<18} {'bytes':>8} {'items':>6} {'sec':>6}  result")
    fails = 0
    for name, typ, code, size, items, dt in rows:
        ok = code == "200" and (items > 0)
        fails += 0 if ok else 1
        print(f"{name:<28} {typ:<12} {code:<18} {size:>8} {items:>6} {dt:>6.1f}  {'OK' if ok else 'FAIL'}")
    print(f"\n{len(rows) - fails}/{len(rows)} OK")
    thebell = next(r for r in rows if r[0].startswith("더벨"))
    print("THEBELL_GOOGLE_NEWS:", "OK" if thebell[2] == "200" and thebell[4] > 0 else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
