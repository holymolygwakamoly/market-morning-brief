"""httpx 기반 텍스트 fetch — 재시도·백오프 포함."""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)


def fetch_text(
    url: str,
    *,
    timeout_s: float,
    retries: int,
    backoff_s: float = 2.0,
    user_agent: str,
    client: httpx.Client | None = None,
) -> str:
    """URL 본문을 텍스트로 가져온다.

    httpx.TransportError, 5xx, 429 응답은 `retries`회까지 재시도한다.
    그 외 4xx는 즉시 예외를 전파한다. `client`를 주입하면 테스트에서 재사용 가능.
    """
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    owns_client = client is None
    c = client if client is not None else httpx.Client(follow_redirects=True)
    last_err: Exception | None = None
    try:
        for attempt in range(retries + 1):
            try:
                resp = c.get(url, headers=headers, timeout=timeout_s)
            except httpx.TransportError as e:
                last_err = e
                logger.warning("fetch_text transport error attempt=%d url=%s err=%s", attempt, url, e)
                if attempt < retries:
                    time.sleep(backoff_s)
                    continue
                raise
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = httpx.HTTPStatusError(
                    f"status {resp.status_code}", request=resp.request, response=resp
                )
                logger.warning(
                    "fetch_text retryable status=%d attempt=%d url=%s", resp.status_code, attempt, url
                )
                if attempt < retries:
                    time.sleep(backoff_s)
                    continue
                raise last_err
            resp.raise_for_status()
            return resp.text
    finally:
        if owns_client:
            c.close()
    # 도달 불가하지만 타입 체커를 위해 마지막 오류를 명시적으로 재발생
    if last_err is not None:
        raise last_err
    raise RuntimeError("fetch_text failed without error")  # pragma: no cover
