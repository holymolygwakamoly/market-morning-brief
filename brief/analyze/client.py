"""Claude Code CLI(`claude -p`) 엔진 래퍼 — 호출 캡·데드라인·백오프·usage 집계.

구독(Max) 기반 실행: API 키·과금 없음. CLI가 출력하는 `total_cost_usd`는 추정치일 뿐 청구되지 않는다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from brief.config import CLAUDE_BIN, COST_SOFT_CAP_USD


class Usage(BaseModel):
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    billed: bool = False


class SkippedForDeadline(Exception):
    """잔여 시간이 최소 실행 시간보다 짧아 호출을 건너뛴다."""


class CallCapExceeded(Exception):
    """stage별 호출 상한에 도달했다. `.last_error`에 마지막 예외를 보관."""

    def __init__(self, stage: str, last_error: BaseException | None) -> None:
        self.stage = stage
        self.last_error = last_error
        super().__init__(f"{stage}: 호출 상한 도달 (마지막 오류: {type(last_error).__name__}: {str(last_error)[:200]})")


class CLIError(Exception):
    """`claude -p` 실패 — 비정상 종료·is_error·structured_output 누락·JSON 파싱 실패."""


def _clean_env() -> dict[str, str]:
    """Claude Code 세션 내부에서 호출돼도 자격 증명이 로드되도록 `CLAUDE*` 환경변수를 제거한다."""
    return {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}


def _default_runner(argv: list[str], user: str, env: dict[str, str], timeout: float) -> Any:
    return subprocess.run(  # noqa: S603 — argv 리스트, shell=False
        argv,
        input=user,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )


def _parse_cli_json(stdout: str, stderr: str) -> dict:
    start = stdout.find("{")
    if start < 0:
        raise CLIError(f"CLI 출력에 JSON 없음. stdout={stdout[-300:]!r} stderr={stderr[-500:]!r}")
    try:
        data = json.loads(stdout[start:])
    except json.JSONDecodeError as e:
        raise CLIError(f"CLI JSON 파싱 실패: {e}. stdout={stdout[-300:]!r} stderr={stderr[-500:]!r}") from e
    if not isinstance(data, dict):
        raise CLIError(f"CLI JSON이 객체가 아님: {type(data).__name__}")
    return data


class LLMClient:
    def __init__(
        self,
        *,
        runner: Callable[[list[str], str, dict[str, str], float], Any] | None = None,
        claude_bin: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runner = runner or _default_runner
        self.claude_bin = claude_bin or CLAUDE_BIN
        self.sleep = sleep
        self.usages: list[Usage] = []
        self.calls: dict[str, int] = {}
        self.errors: dict[str, list[str]] = {}

    def _resolve_bin(self) -> str:
        path = shutil.which(self.claude_bin)
        if not path:
            raise CLIError(
                f"Claude Code CLI를 찾을 수 없음: {self.claude_bin!r}. "
                "`claude`를 설치·로그인하거나 CLAUDE_BIN 환경변수로 경로를 지정하세요."
            )
        return path

    def call(
        self,
        stage: str,
        fn: Callable[[float], Any],
        *,
        max_calls: int,
        deadline: float,
        min_seconds: float = 45.0,
        budget_s: float = 300.0,
        backoffs: tuple[float, ...] = (5, 15),
    ) -> Any:
        """`fn(timeout)`을 최대 `max_calls`회 시도한다.

        - 매 시도 전 잔여 시간 < min_seconds 이면 SkippedForDeadline
        - 호출 timeout = min(budget_s, 잔여−60s) — 한 단계가 행(hang)으로 전체 데드라인을 먹지 않게
        - 예외(CLIError·TimeoutExpired·ValidationError 등)는 기록 후 백오프(잔여 시간 내) 뒤 재시도
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
                timeout = max(10.0, min(budget_s, remaining - 60))
                return fn(timeout)
            except SkippedForDeadline:
                raise
            except Exception as e:  # noqa: BLE001 — CLI/검증 오류 모두 재시도 대상
                last_error = e
                self.errors.setdefault(stage, []).append(f"{type(e).__name__}: {str(e)[:200]}")
                if attempt + 1 < max_calls:
                    backoff = backoffs[min(attempt, len(backoffs) - 1)]
                    wait = min(backoff, deadline - time.monotonic() - min_seconds)
                    if wait > 0:
                        self.sleep(wait)
        raise CallCapExceeded(stage, last_error)

    def structured_once(
        self,
        stage: str,
        *,
        system: str,
        user: str,
        output_format: type[BaseModel],
        model: str,
        timeout: float,
    ) -> BaseModel:
        """`claude -p --json-schema` 1회 실행 → `structured_output`을 `output_format`으로 검증해 반환.

        실패(비정상 종료·is_error·structured_output 누락·JSON 파싱 실패)는 CLIError,
        스키마 불일치는 pydantic ValidationError. usage는 CLI JSON을 파싱한 즉시 기록한다.
        """
        argv = [
            self._resolve_bin(),
            "-p",
            "--model", model,
            "--no-session-persistence",
            "--tools", "",
            "--output-format", "json",
            "--json-schema", json.dumps(output_format.model_json_schema(), ensure_ascii=False),
            "--system-prompt", system,
        ]
        proc = self.runner(argv, user, _clean_env(), timeout)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if proc.returncode != 0:
            raise CLIError(
                f"claude 종료 코드 {proc.returncode}. stderr={stderr[-500:]!r} stdout={stdout[-300:]!r}"
            )
        data = _parse_cli_json(stdout, stderr)
        self.record_usage(stage, model, data)
        if data.get("is_error"):
            raise CLIError(
                f"claude is_error. result={str(data.get('result'))[:500]!r} stderr={stderr[-500:]!r}"
            )
        out = data.get("structured_output")
        if not isinstance(out, dict):
            raise CLIError(
                f"structured_output 누락. result={str(data.get('result'))[:500]!r} stderr={stderr[-500:]!r}"
            )
        return output_format.model_validate(out)

    def structured(
        self,
        stage: str,
        *,
        system: str,
        user: str,
        output_format: type[BaseModel],
        model: str,
        deadline: float,
        max_calls: int,
        budget_s: float,
        min_seconds: float = 45.0,
        check: Callable[[Any], None] | None = None,
    ) -> BaseModel:
        """`structured_once`를 `call()`의 캡·데드라인·백오프 아래 재시도한다.

        `check(result)`가 예외를 던지면 그 시도는 실패로 간주해 재시도한다.
        """

        def fn(timeout: float) -> BaseModel:
            result = self.structured_once(
                stage, system=system, user=user, output_format=output_format, model=model, timeout=timeout
            )
            if check is not None:
                check(result)
            return result

        return self.call(
            stage, fn, max_calls=max_calls, deadline=deadline, min_seconds=min_seconds, budget_s=budget_s
        )

    def record_usage(self, stage: str, model: str, data: dict) -> Usage:
        usage = data.get("usage") or {}
        u = Usage(
            stage=stage,
            model=model,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cost_usd=float(data.get("total_cost_usd") or 0.0),
            billed=False,
        )
        self.usages.append(u)
        return u

    def total_cost_usd(self) -> float:
        """CLI 추정 비용 합계(청구되지 않음, 참고용)."""
        return sum(u.cost_usd for u in self.usages)

    def warnings(self) -> list[str]:
        return ["cost_over_soft_cap"] if self.total_cost_usd() > COST_SOFT_CAP_USD else []

    def summary(self) -> dict:
        stages: dict[str, dict] = {}
        for u in self.usages:
            s = stages.setdefault(
                u.stage,
                {"model": u.model, "input_tokens": 0, "output_tokens": 0, "cost_estimate_usd": 0.0},
            )
            s["input_tokens"] += u.input_tokens
            s["output_tokens"] += u.output_tokens
            s["cost_estimate_usd"] += u.cost_usd
        for stage, n in self.calls.items():
            stages.setdefault(
                stage,
                {"model": None, "input_tokens": 0, "output_tokens": 0, "cost_estimate_usd": 0.0},
            )["calls"] = n
        for s in stages.values():
            s.setdefault("calls", 0)
            s["cost_estimate_usd"] = round(s["cost_estimate_usd"], 6)
        models = {u.model for u in self.usages}
        return {
            "engine": "claude-cli",
            "model": next(iter(models)) if len(models) == 1 else (sorted(models) or None),
            "billed": False,
            "stages": stages,
            "total_input_tokens": sum(u.input_tokens for u in self.usages),
            "total_output_tokens": sum(u.output_tokens for u in self.usages),
            "total_cost_usd": round(self.total_cost_usd(), 6),
            "total_calls": sum(self.calls.values()),
            "warnings": self.warnings(),
        }
