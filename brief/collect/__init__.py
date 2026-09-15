"""수집 계층 진입점 — 소스 병렬 수집 + Article 평탄화."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Any

from brief.config import ADAPTER_TYPES

from .base import Article, SourceResult
from .google_news import GoogleNewsAdapter
from .rss import RssAdapter

logger = logging.getLogger(__name__)

_ADAPTER_REGISTRY: dict[str, type] = {
    "rss": RssAdapter,
    "google_news": GoogleNewsAdapter,
}


def _build_adapter(source_cfg: dict, defaults: dict) -> Any:
    adapter_type = source_cfg.get("type")
    if adapter_type not in ADAPTER_TYPES:
        raise ValueError(f"unknown adapter type: {adapter_type!r}")
    adapter_cls = _ADAPTER_REGISTRY.get(adapter_type)
    if adapter_cls is None:
        raise ValueError(f"no adapter registered for type: {adapter_type!r}")
    return adapter_cls(source_cfg, defaults)


def _adapter_name(adapter: Any) -> str:
    cfg = getattr(adapter, "cfg", None)
    if isinstance(cfg, dict) and "name" in cfg:
        return cfg["name"]
    return getattr(adapter, "name", "unknown")


def collect_all(
    cfg: dict,
    *,
    deadline_s: float = 240.0,
    max_workers: int = 8,
    adapters: list[Any] | None = None,
) -> list[SourceResult]:
    """cfg["sources"]를 병렬 수집한다. 예외/타임아웃은 소스별로 격리한다."""
    defaults = cfg.get("defaults", {})
    if adapters is None:
        adapters = [_build_adapter(s, defaults) for s in cfg.get("sources", [])]

    results: list[SourceResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(a.fetch): a for a in adapters}
        pending = set(future_map)
        try:
            for future in as_completed(future_map, timeout=deadline_s):
                pending.discard(future)
                adapter = future_map[future]
                try:
                    result = future.result()
                except Exception as e:  # noqa: BLE001
                    logger.warning("source failed: %s: %s", _adapter_name(adapter), e)
                    result = SourceResult(
                        name=_adapter_name(adapter), ok=False, count=0, error=str(e), articles=[], elapsed_s=0.0
                    )
                results.append(result)
        except FuturesTimeoutError:
            pass
        for future in pending:
            adapter = future_map[future]
            future.cancel()
            results.append(
                SourceResult(
                    name=_adapter_name(adapter),
                    ok=False,
                    count=0,
                    error="deadline exceeded",
                    articles=[],
                    elapsed_s=deadline_s,
                )
            )
    return results


def articles_from(results: list[SourceResult]) -> list[Article]:
    """SourceResult 목록에서 Article을 평탄화한다."""
    articles: list[Article] = []
    for r in results:
        articles.extend(r.articles)
    return articles
