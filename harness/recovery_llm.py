"""Harness 7 — Recovery (모델 쪽, 담당: R2).

LLM 429·503: 2회 재시도(1초, 3초 대기) → 대체 모델 → 그래도 실패하면 LLMUnavailable(status FAILED).
모든 실패는 audit_logs에 trace_id와 함께 남긴다. 도구 TIMEOUT 재시도는 harness/recovery_tools.py(R4).
"""
import logging
import os
import time
from typing import Any, Callable

from harness.tracing import write_audit_log

log = logging.getLogger(__name__)

LLM_RETRY_DELAYS = (1, 3)
RETRYABLE_STATUS = (429, 503)


class LLMUnavailable(Exception):
    """기본·대체 모델 모두 실패 → 호출한 쪽이 status FAILED로 저장."""


class FakeLLMError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"가짜 LLM 오류 {status_code}")
        self.status_code = status_code


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def is_retryable(exc: BaseException) -> bool:
    return _status_code(exc) in RETRYABLE_STATUS


def maybe_raise_fake_error(model_role: str) -> None:
    """FAKE_LLM_ERROR=503 이면 기본 모델 호출만 실패시킨다 (대체 모델은 성공해야 시연이 된다)."""
    code = os.getenv("FAKE_LLM_ERROR", "").strip()
    if code.isdigit() and model_role == "primary":
        raise FakeLLMError(int(code))


def call_llm_with_recovery(
    primary: Callable[[], Any],
    fallback: Callable[[], Any] | None = None,
    trace_id: str | None = None,
    target: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """primary()를 부르고, 429·503이면 1초·3초 간격으로 재시도 → fallback() → LLMUnavailable."""
    last: BaseException | None = None
    for attempt in range(len(LLM_RETRY_DELAYS) + 1):
        try:
            maybe_raise_fake_error("primary")
            return primary()
        except Exception as exc:  # noqa: BLE001
            if not is_retryable(exc):
                raise
            last = exc
            _audit("llm_retry", target, trace_id, exc, attempt=attempt + 1)
            if attempt < len(LLM_RETRY_DELAYS):
                sleep(LLM_RETRY_DELAYS[attempt])

    if fallback is not None:
        _audit("llm_fallback", target, trace_id, last)
        try:
            return fallback()
        except Exception as exc:  # noqa: BLE001
            last = exc
    _audit("llm_failed", target, trace_id, last)
    raise LLMUnavailable(str(last)) from last


def _audit(action: str, target: str | None, trace_id: str | None, error: Any, **extra: Any) -> None:
    try:
        write_audit_log("system", action, target, {"error": str(error), **extra}, trace_id)
    except Exception:  # noqa: BLE001 — 기록 실패가 본 작업을 막으면 안 된다
        log.exception("audit_logs 기록 실패: %s", action)
