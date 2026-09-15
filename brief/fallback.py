"""킬 폴백 CLI — `python -m brief.fallback --reason runner_killed [--date YYYY-MM-DD] [--docs docs]`.

stdlib + jinja만 import(anthropic 없음). 어떤 경우에도 exit 0.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from brief.config import KST
from brief.render.fallback import write_error_banner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brief.fallback")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘 KST)")
    parser.add_argument("--docs", default="docs")
    args = parser.parse_args(argv)
    now = datetime.now(KST)
    date = args.date or now.strftime("%Y-%m-%d")
    try:
        out = write_error_banner(Path(args.docs), reason=args.reason, now_kst=now, date=date)
        print(f"fallback: wrote {out} ({args.reason})")
    except BaseException as e:  # noqa: BLE001 — 폴백은 절대 실패하지 않는다
        print(f"fallback: error: {e!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
