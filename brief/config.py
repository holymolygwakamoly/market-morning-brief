"""전역 설정 — KST 타임존, 기사 상한, 소스 설정 로더."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

KST = ZoneInfo("Asia/Seoul")

MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "200"))
PER_SOURCE_MAX = 40

ADAPTER_TYPES = {"rss", "google_news", "yahoo_chart"}

_DEFAULT_SOURCES_PATH = Path(__file__).resolve().parent / "sources.yaml"


def load_sources(path: str | Path | None = None) -> dict:
    """`sources.yaml`을 로드해 dict로 반환한다."""
    p = Path(path) if path is not None else _DEFAULT_SOURCES_PATH
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)
