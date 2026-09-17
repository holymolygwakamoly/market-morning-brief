"""전역 설정 — KST 타임존, 기사 상한, 소스 설정 로더."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

KST = ZoneInfo("Asia/Seoul")

MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "200"))
PER_SOURCE_MAX = 40

ADAPTER_TYPES = {"rss", "google_news"}

_DEFAULT_SOURCES_PATH = Path(__file__).resolve().parent / "sources.yaml"


def load_sources(path: str | Path | None = None) -> dict:
    """`sources.yaml`을 로드해 dict로 반환한다."""
    p = Path(path) if path is not None else _DEFAULT_SOURCES_PATH
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


# --- 분석 계층(Step 3) ---------------------------------------------------------
# LLM 엔진: Claude Code CLI(`claude -p`, Max 구독). 모델 alias(sonnet/opus/haiku) 또는 전체 ID.
BRIEF_MODEL = os.getenv("BRIEF_MODEL", "opus")  # 보고서 본문(stage2) 모델. 사용자 결정: opus
# 사용자 결정: 보고서에 쓰는 모든 토큰은 opus. (stage1 태깅은 느리므로 예산 480s)
STAGE1_MODEL = os.getenv("STAGE1_MODEL", BRIEF_MODEL)
STAGE2_MODEL = os.getenv("STAGE2_MODEL", BRIEF_MODEL)
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")
STAGE1_MAX_CALLS = 2
STAGE2_MAX_CALLS = 3
AI_KEYWORDS = [
    "AI", "인공지능", "반도체", "HBM", "GPU", "엔비디아", "NVIDIA", "데이터센터",
    "오픈AI", "OpenAI", "LLM", "Anthropic", "생성형",
]
