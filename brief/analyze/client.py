"""Anthropic 클라이언트 래퍼 — 호출 캡·데드라인·백오프·usage/비용 집계."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from anthropic import Anthropic
from pydantic import BaseModel

from brief.config import COST_SOFT_CAP_USD, PRICES_PER_MTOK

_DEFAULT_PRICE_MODEL = "claude-sonnet-5"


class Usage(BaseModel):
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class SkippedForDeadline(Exception):
    """잔여 시간이 최소 실행 시간보다 짧아 호출을 건너뛴다."""


class CallCapExceeded(Exception):
    """stage별 호출 상한에 도달했다. `.last_error`에 마지막 예외를 보관."""

    def __init__(self, stage: str, last_error: BaseException | None) -> None:
        self.stage = stage
        self.last_error = last_error
        super().__init__(f"{stage}: 호출 상한 도달 (마지막 오류: {last_error!r})")


def _price(model: str) -> tuple[float, float]:
    for key, price in PRICES_PER_MTOK.items():
        if model == key or model.startswith(key):
            return price
    return PRICES_PER_MTOK[_DEFAULT_PRICE_MODEL]


class LLMClient:
    def __init__(
        self,
        client: Any | None = None,
        api_key: str | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client or Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"), max_retries=0
        )
        self.sleep = sleep
        self.usages: list[Usage] = []
        self.calls: dict[str, int] = {}
        self.errors: dict[str, list[str]] = {}

    def call(
        self,
        stage: str,
        fn: Callable[[Any], Any],
        *,
        max_calls: int,
        deadline: float,
        min_seconds: float = 45.0,
        backoffs: tuple[float, ...] = (5, 15),
    ) -> Any:
        """`fn(client_with_timeout)`을 최대 `max_calls`회 시도한다.

        - 매 시도 전 잔여 시간 < min_seconds 이면 SkippedForDeadline
        - 예외(API 오류·ValidationError 등)는 기록 후 백오프(잔여 시간 내) 뒤 재시도
        - 상한 도달 시 CallCapExceeded(last_error)
        """
        last_error: BaseException | None = None
        for attempt in range(max_calls):
            remaining = deadline - time.monotonic()
            if remaining < min_seconds:
                raise SkippedForDeadline(
                    f"{stage}: 잔여 {remaining:.0f}s < 최소 {min_seconds:.0f}s"
                )
            self.calls[stage] = self.calls.get(stage, 0) + 1
            try:
                return fn(self.client.with_options(timeout=max(10.0, remaining - 5)))
            except SkippedForDeadline:
                raise
            except Exception as e:  # noqa: BLE001 — API/검증 오류 모두 재시도 대상
                last_error = e
                self.errors.setdefault(stage, []).append(f"{type(e).__name__}: {e}")
                if attempt + 1 < max_calls:
                    backoff = backoffs[min(attempt, len(backoffs) - 1)]
                    wait = min(backoff, deadline - time.monotonic() - min_seconds)
                    if wait > 0:
                        self.sleep(wait)
        raise CallCapExceeded(stage, last_error)

    def record_usage(self, stage: str, model: str, message: Any) -> Usage:
        usage = message.usage
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)
        p_in, p_out = _price(model)
        u = Usage(
            stage=stage,
            model=model,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=(inp * p_in + out * p_out) / 1_000_000,
        )
        self.usages.append(u)
        return u

    def total_cost_usd(self) -> float:
        return sum(u.cost_usd for u in self.usages)

    def warnings(self) -> list[str]:
        return ["cost_over_soft_cap"] if self.total_cost_usd() > COST_SOFT_CAP_USD else []

    def summary(self) -> dict:
        stages: dict[str, dict] = {}
        for u in self.usages:
            s = stages.setdefault(
                u.stage,
                {"model": u.model, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            )
            s["input_tokens"] += u.input_tokens
            s["output_tokens"] += u.output_tokens
            s["cost_usd"] += u.cost_usd
        for stage, n in self.calls.items():
            stages.setdefault(
                stage, {"model": None, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            )["calls"] = n
        for s in stages.values():
            s.setdefault("calls", 0)
            s["cost_usd"] = round(s["cost_usd"], 6)
        return {
            "stages": stages,
            "total_input_tokens": sum(u.input_tokens for u in self.usages),
            "total_output_tokens": sum(u.output_tokens for u in self.usages),
            "total_cost_usd": round(self.total_cost_usd(), 6),
            "total_calls": sum(self.calls.values()),
            "warnings": self.warnings(),
        }
