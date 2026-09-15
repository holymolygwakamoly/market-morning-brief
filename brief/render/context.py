"""렌더/상태 공용 컨텍스트 — RunStatus (docs/status/{date}.json 직렬화)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Literal

Result = Literal["success", "degraded", "failed"]


@dataclass
class RunStatus:
    date: str
    run_kind: str = "main"
    started_at_kst: str = ""
    finished_at_kst: str = ""
    result: Result = "failed"
    sources: list[dict] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    coverage_ok: bool = False
    stage1_degraded: bool = False
    llm: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)
    unverified_numbers: list[str] = field(default_factory=list)
    error: str | None = None
    article_count: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "RunStatus":
        data = json.loads(text)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
