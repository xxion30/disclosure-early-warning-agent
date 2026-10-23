"""모델 쪽 Harness 연결 (담당: R2): 입력 준비(마스킹·자르기·토큰 예산), 설명문 검사, LLM 재시도·대체 모델.

assembly.py에서 분리했다. 도구 쪽(recover_tool 등)은 agent/adapters/harness_tools.py(R4).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from harness import context as ctx
from harness import guardrails, masking
from harness import recovery_llm as recovery
from harness.tracing import audit_system
from agent.ports import AgentError

PROTECTED_KEYS = frozenset({"source", "rcept_no", "location", "quote", "customer_id",
                            "item", "flag", "stage", "disclosure_type"})


def _shrink_strings(obj: Any, limit: int) -> Any:
    if isinstance(obj, dict):
        return {k: v if k in PROTECTED_KEYS else _shrink_strings(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shrink_strings(v, limit) for v in obj]
    if isinstance(obj, str) and len(obj) > limit:
        return ctx.truncate_tool_result(obj, limit)
    return obj


def _longest_string(obj: Any) -> int:
    if isinstance(obj, dict):
        return max((_longest_string(v) for v in obj.values()), default=0)
    if isinstance(obj, list):
        return max((_longest_string(v) for v in obj), default=0)
    return len(obj) if isinstance(obj, str) else 0


async def prepare_input(payload: dict) -> dict:
    """모델에 보내기 직전: 개인정보 마스킹 → 긴 문자열 4,000자 자르기 → 입력 6,000토큰 안으로 줄이기."""
    masked = masking.mask_obj(payload)

    def check_identifiers(original, prepared):
        if isinstance(original, dict):
            for key, value in original.items():
                if key in PROTECTED_KEYS and value != prepared[key]:
                    # Never call a masked fragment a verbatim citation; never
                    # restore suspected PII just to make validation pass.
                    raise AgentError("출처 식별자·인용 마스킹 충돌: 원문 확인 필요")
                check_identifiers(value, prepared[key])
        elif isinstance(original, list):
            for a, b in zip(original, prepared):
                check_identifiers(a, b)

    check_identifiers(payload, masked)
    prepared = _shrink_strings(masked, ctx.TOOL_RESULT_MAX_CHARS)
    limit = ctx.TOOL_RESULT_MAX_CHARS
    for _ in range(12):
        if ctx.estimate_tokens(json.dumps(prepared, ensure_ascii=False)) <= ctx.TOKEN_BUDGET:
            break
        limit = max(50, min(limit, _longest_string(prepared)) // 2)
        prepared = _shrink_strings(prepared, limit)
    if ctx.estimate_tokens(json.dumps(prepared, ensure_ascii=False)) > ctx.TOKEN_BUDGET:
        raise AgentError("원문 식별자를 보존한 입력 예산 초과")
    return prepared


async def check_message(message: str) -> list[str]:
    return guardrails.check_message(message)


async def recover_llm(primary, fallback):
    """429·503이면 1초·3초 간격 2회 재시도 → 대체 모델 → 그래도 실패하면 LLMUnavailable (D의 recovery 규칙).

    FAKE_LLM_ERROR 주입은 agent.llm.StructuredLLM이 이미 하므로 여기서는 하지 않는다(이중 주입 방지).
    """
    last: BaseException | None = None
    delays = recovery.LLM_RETRY_DELAYS
    for attempt in range(len(delays) + 1):
        try:
            return await primary()
        except Exception as exc:  # noqa: BLE001
            if not recovery.is_retryable(exc):
                raise
            last = exc
            await audit_system("llm_retry", None, exc, attempt=attempt + 1)
            if attempt < len(delays):
                await asyncio.sleep(delays[attempt])
    await audit_system("llm_fallback", None, last)
    try:
        return await fallback()
    except Exception as exc:  # noqa: BLE001
        last = exc
    await audit_system("llm_failed", None, last)
    raise recovery.LLMUnavailable(str(last)) from last
