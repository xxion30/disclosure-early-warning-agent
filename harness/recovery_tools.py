"""Harness 7 — Recovery (도구 쪽, 담당: R4).

MCP 도구 TIMEOUT: 1회 재시도. 그 밖의 오류는 그대로 돌려준다. 모든 실패는 audit_logs에 trace_id와 함께 남긴다.
모델 쪽 재시도·대체 모델은 harness/recovery_llm.py(R2).
"""
import logging
from typing import Any, Callable

from harness.tracing import write_audit_log

log = logging.getLogger(__name__)


def call_tool_with_retry(
    call: Callable[[], dict],
    trace_id: str | None = None,
    target: str | None = None,
) -> dict:
    """MCP 도구 호출. 응답이 TIMEOUT 오류면 1회 재시도하고, 그 밖의 오류는 그대로 돌려준다.

    반환값은 {"ok": ...} 형식 그대로다. 호출한 노드가 ok=False를 State.error에 남기고 MANUAL_CHECK로 보낸다.
    """
    result = call()
    if not result.get("ok") and result.get("error", {}).get("code") == "TIMEOUT":
        _audit("tool_timeout_retry", target, trace_id, result["error"].get("message"))
        result = call()
    if not result.get("ok"):
        _audit("tool_failed", target, trace_id, result.get("error"))
    return result


def _audit(action: str, target: str | None, trace_id: str | None, error: Any, **extra: Any) -> None:
    try:
        write_audit_log("system", action, target, {"error": str(error), **extra}, trace_id)
    except Exception:  # noqa: BLE001 — 기록 실패가 본 작업을 막으면 안 된다
        log.exception("audit_logs 기록 실패: %s", action)
