""".env 키 로더 — 값은 출력하지 않는다.

우선순위: 프로세스 환경변수 > 프로젝트 `.env` > `.env`의 `SHARED_ENV_FILE`이 가리키는 공용 env 파일
(예: `SHARED_ENV_FILE=../자산운용사 동향/.env`, 상대 경로는 프로젝트 루트 기준). `.env`는 gitignore.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_env(root: Path | None = None) -> dict[str, str]:
    """프로젝트 `.env` + 공용 env 파일을 합친 dict(프로젝트 값이 우선). os.environ은 건드리지 않는다."""
    root = root or PROJECT_ROOT
    local = _parse(root / ".env")
    shared_ref = local.get("SHARED_ENV_FILE") or os.environ.get("SHARED_ENV_FILE")  # 이 프로젝트 설정이 우선
    shared: dict[str, str] = {}
    if shared_ref:
        p = Path(shared_ref)
        shared = _parse(p if p.is_absolute() else root / p)
    return {**shared, **local}


def get_keys(names: list[str], root: Path | None = None) -> list[str] | None:
    """names 전부가 있으면 값 목록, 하나라도 비면 None."""
    file_env: dict[str, str] | None = None
    vals: list[str] = []
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if not v:
            if file_env is None:
                file_env = load_env(root)
            v = (file_env.get(n) or "").strip()
        if not v:
            return None
        vals.append(v)
    return vals
