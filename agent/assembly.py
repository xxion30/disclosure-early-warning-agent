"""agent.Dependencies 조립: B의 MCP 도구 + A의 모델 + D의 Harness + B의 저장 도구를 한곳에서 묶는다.

API(`api/graph_adapter.default_dependencies`)는 여기서 만든 deps를 받기만 한다.
각 조각의 경계는 agent/INTEGRATION.md, 도구 계약은 CLAUDE.md 4장과 mcp_servers/business/README.md.

    deps = await build_dependencies()          # business-mcp(8001)·rag-mcp(8002)가 떠 있어야 한다
    deps = await build_dependencies(model=...)  # 테스트: 모델(또는 tools)만 바꿔 끼운다
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
from typing import Any

from sqlalchemy import select

from agent.execution import invoke_tool
from agent.harness_adapter import HarnessBindings
from agent.persistence import ToolAssessmentStore, ToolTaskCreator
from agent.ports import AgentError, Dependencies
from agent.rules import load_classifier
from harness import context as ctx
from harness import guardrails, masking, recovery
from harness.tracing import write_audit_log
from mcp_servers.business.rules.loader import DEFAULT_PATH, load_rules
from shared.config import get_settings
from shared.db import get_session
from shared.models import AuditLog, Customer, RiskAssessment
from shared.schemas import ToolResponse

log = logging.getLogger("agent.assembly")


# ------------------------------------------------------------------ Harness (D의 모듈을 A의 async 계약에 맞춤)
def _trace_id() -> str | None:
    """진행 중인 LangSmith trace의 root id (없으면 None)."""
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        return str(run.trace_id) if run is not None else None
    except Exception:  # noqa: BLE001 — 관측 실패가 본 작업을 막으면 안 된다
        return None


async def _audit_system(action: str, target: str | None, error: Any, **extra: Any) -> None:
    try:
        await asyncio.to_thread(
            write_audit_log, "system", action, target, {"error": str(error), **extra}, _trace_id()
        )
    except Exception:  # noqa: BLE001
        log.exception("audit_logs 기록 실패: %s", action)


def _shrink_strings(obj: Any, limit: int) -> Any:
    if isinstance(obj, dict):
        return {k: _shrink_strings(v, limit) for k, v in obj.items()}
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
    prepared = _shrink_strings(masking.mask_obj(payload), ctx.TOOL_RESULT_MAX_CHARS)
    limit = ctx.TOOL_RESULT_MAX_CHARS
    for _ in range(12):
        if ctx.estimate_tokens(json.dumps(prepared, ensure_ascii=False)) <= ctx.TOKEN_BUDGET:
            break
        limit = max(50, min(limit, _longest_string(prepared)) // 2)
        prepared = _shrink_strings(prepared, limit)
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
            await _audit_system("llm_retry", None, exc, attempt=attempt + 1)
            if attempt < len(delays):
                await asyncio.sleep(delays[attempt])
    await _audit_system("llm_fallback", None, last)
    try:
        return await fallback()
    except Exception as exc:  # noqa: BLE001
        last = exc
    await _audit_system("llm_failed", None, last)
    raise recovery.LLMUnavailable(str(last)) from last


async def recover_tool(operation) -> ToolResponse:
    """MCP 도구 TIMEOUT은 1회 재시도, 나머지 오류는 그대로 돌려준다."""
    result = await operation()
    if not result.ok and result.error and result.error.code == "TIMEOUT":
        await _audit_system("tool_timeout_retry", None, result.error.message)
        result = await operation()
    if not result.ok:
        await _audit_system("tool_failed", None, result.error)
    return result


async def event(name: str, payload: dict) -> None:
    log.info("agent_event %s %s", name, {k: v for k, v in payload.items() if k != "error"})


def default_harness() -> HarnessBindings:
    return HarnessBindings(
        prepare_input=prepare_input, check_message=check_message, recover_llm=recover_llm,
        recover_tool=recover_tool, event=event,
    )


# ------------------------------------------------------------------ 저장·감사·업무 생성
class AssessmentStore(ToolAssessmentStore):
    """A의 ToolAssessmentStore + 점수 계산 전에 멈춘 평가(MANUAL_CHECK·FAILED)의 저장.

    save_assessment 도구는 rcept_no 기준 upsert이고, 점수 없이도 RUNNING·FAILED·MANUAL_CHECK로 저장된다.
    """

    async def save(self, state, status):
        assessment_id = await self._save(state, status)
        await self._release_after_save(state, status)
        return assessment_id

    async def _release_after_save(self, state, status):
        """운영(RAG_INDEX_MODE=ephemeral): 경보를 저장한 뒤 이 공시의 임시 색인과 DB 본문을 지운다.
        실패(MANUAL_CHECK·FAILED)는 해결될 때까지 남겨 두고, 정리가 실패해도 처리 결과는 바꾸지 않는다."""
        if status not in ("WAITING_APPROVAL", "DONE") or get_settings().RAG_INDEX_MODE != "ephemeral":
            return
        rcept_no = (state.get("disclosure") or {}).get("rcept_no")
        if not rcept_no:
            return
        try:
            released = await invoke_tool(self.tools, self.harness, "release_disclosure_index", {"rcept_no": rcept_no})
            await invoke_tool(self.tools, self.harness, "purge_disclosure_text",
                              {"rcept_no": rcept_no, "content_hash": released.get("content_sha256")})
        except Exception as exc:  # noqa: BLE001 — 정리는 부가 작업이라 평가 저장을 되돌리지 않는다
            log.warning("처리 후 색인·본문 정리 실패 %s: %s: %s", rcept_no, type(exc).__name__, exc)

    async def _save(self, state, status):
        if "score_result" in state:
            return await super().save(state, status)
        disclosure = state.get("disclosure") or {}
        if not disclosure.get("corp_code") or not disclosure.get("rcept_no"):
            raise AgentError("공시 정보를 받기 전에 실패해 평가를 저장할 수 없습니다")
        result = await invoke_tool(self.tools, self.harness, "save_assessment", {
            "corp_code": disclosure["corp_code"], "rcept_no": disclosure["rcept_no"], "status": status,
        })
        return result["assessment_id"]


def _write_audit(state: dict, action: str, payload: dict) -> None:
    key = payload.get("idempotency_key")
    target = (state.get("disclosure") or {}).get("rcept_no")
    if key:
        with get_session() as s:
            exists = s.scalars(
                select(AuditLog.log_id).where(
                    AuditLog.action == action, AuditLog.payload["idempotency_key"].as_string() == key
                )
            ).first()
        if exists:
            return  # 재개로 같은 노드가 다시 실행돼도 한 번만 남긴다
    write_audit_log(payload.get("pb_id") or "agent", action, target, payload, state.get("trace_id"))


async def audit(state: dict, action: str, payload: dict) -> None:
    await asyncio.to_thread(_write_audit, state, action, payload)


def _owns(pb_id: str, customer_id: str, assessment_id: int) -> bool:
    with get_session() as s:
        customer = s.get(Customer, customer_id)
        return bool(customer and customer.pb_id == pb_id and s.get(RiskAssessment, assessment_id))


async def authorize_task(pb_id: str, customer_id: str, assessment_id: int) -> bool:
    """담당 PB의 고객이고 존재하는 평가일 때만 업무 생성을 허용한다 (API도 먼저 검사한다)."""
    return await asyncio.to_thread(_owns, pb_id, customer_id, assessment_id)


# ------------------------------------------------------------------ 사실 → 규칙 입력
def signal_input(extracted: dict) -> list[dict]:
    """검증된 사실을 calculate_risk_score의 new_signals로 바꾼다. 점수는 넣지 않는다(B 규칙표가 정한다)."""
    signals = []
    for fact in extracted.get("facts", []):
        evidence = list(fact.get("evidence") or [])
        signals.append({
            "signal_type": str(fact["signal_type"]).strip().upper(),  # 모델이 소문자로 써도 규칙표 이름에 맞춘다
            "detail": dict(fact.get("values") or {}),
            "evidence": evidence,
            "rcept_no": evidence[0]["rcept_no"] if evidence else None,
        })
    return signals


# ------------------------------------------------------------------ 모델
def _openai_chat(model: str, api_key: str):
    """OpenAI strict 구조화 출력은 dict[str, Any] 필드(facts.values, drafts)를 거부한다.
    A의 스키마를 바꾸지 않고 function calling 방식으로 쓴다."""
    from langchain_openai import ChatOpenAI

    class FunctionCallingChatOpenAI(ChatOpenAI):
        def with_structured_output(self, schema=None, **kwargs):
            kwargs.setdefault("method", "function_calling")
            return super().with_structured_output(schema, **kwargs)

    return FunctionCallingChatOpenAI(model=model, api_key=api_key)


def build_model(harness, settings):
    """모델 구성. 기본은 LLM_PRIMARY, 장애 시 LLM_FALLBACK (OpenAI).

    - LLM_PRIMARY가 `claude…`이고 ANTHROPIC_API_KEY가 있으면 Anthropic을 기본으로 쓴다.
    - LLM_PRIMARY가 `gpt…`이면 OpenAI 모델을 기본으로 쓴다 (Anthropic 키가 필요 없다).
    - LLM_PRIMARY가 `claude…`인데 Anthropic 키가 없으면 LLM_FALLBACK(OpenAI)로 대신한다.
    OpenAI만 쓰면 503 → 대체 모델 전환은 같은 회사 모델 사이에서 일어난다(재시도·전환 동작 자체는 같다).
    """
    from agent.llm import StructuredLLM

    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY가 .env에 필요합니다 (대체 모델 또는 OpenAI 전용 구성)")
    fallback = _openai_chat(settings.LLM_FALLBACK, settings.OPENAI_API_KEY)
    name = settings.LLM_PRIMARY
    if name.startswith("claude") and settings.ANTHROPIC_API_KEY:
        from langchain_anthropic import ChatAnthropic

        primary = ChatAnthropic(model=name, api_key=settings.ANTHROPIC_API_KEY)
    elif name.startswith("claude"):
        log.warning("ANTHROPIC_API_KEY 없음 → OpenAI(%s)를 기본 모델로 사용", settings.LLM_FALLBACK)
        primary = fallback
    else:
        primary = _openai_chat(name, settings.OPENAI_API_KEY)
    return StructuredLLM(primary, fallback, harness, settings.FAKE_LLM_ERROR)


def snap_quote(quote: str, text: str) -> str | None:
    """AI 인용을 원문에서 한 글자도 바뀌지 않은 연속 구간으로 맞춘다.

    인용이 이미 원문 안에 있으면 그대로 둔다. AI가 떨어진 문장을 이어 붙이거나 일부를 바꿔 쓴 경우에는,
    원문과 가장 길게 겹치는 연속 구간(원문 그대로)을 인용으로 쓴다. 겹침이 너무 짧으면 None(보정하지 않음).
    """
    if quote in text:
        return quote
    m = difflib.SequenceMatcher(None, quote, text, autojunk=False).find_longest_match(0, len(quote), 0, len(text))
    if m.size >= max(15, len(quote) // 2):
        return text[m.b : m.b + m.size].strip() or None
    return None


def best_chunk(evidence: dict, chunks: list[dict]) -> tuple[dict | None, str | None]:
    """인용이 가장 잘 들어맞는 검색 조각과, 그 조각에서 원문 그대로로 맞춘 인용을 찾는다."""
    # 이미 위치가 맞는 조각을 먼저 본다 (대부분 여기서 끝난다)
    same = [c for c in chunks if c.get("location") == evidence["location"] and c.get("source") == evidence["source"]]
    best: tuple[dict | None, str | None, int] = (None, None, 0)
    for c in same + [c for c in chunks if c not in same]:
        snapped = snap_quote(evidence["quote"], c["text"])
        if snapped and len(snapped) > best[2]:
            best = (c, snapped, len(snapped))
        if snapped == evidence["quote"]:
            return c, snapped
    return best[0], best[1]


class KnownSignalsModel:
    """모델 출력을 코드가 보증하는 값으로 맞춘다.

    - extract_facts: 규칙표에 없는 signal_type을 걸러내고(AI가 신호 이름을 지어낼 수 없게), 인용을 원문 그대로의 연속 구간으로 맞춘다.
    - explain_risk: 인용(evidence)은 점수 내역에 B 도구가 담아 준 원문 그대로를 붙인다. AI는 설명 문장만 쓴다.
      (AI가 긴 인용문을 한 글자씩 똑같이 베끼지 못해 검증에서 멈추는 일을 막는다.)
    """

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def generate(self, prompt, schema, payload):
        result = await self.inner.generate(prompt, schema, payload)
        if prompt == "explain_risk":
            breakdown = payload.get("score_result", {}).get("breakdown", [])
            if any(not row.get("evidence") for row in breakdown):
                # 근거 인용이 없는 점수 항목이 있다. 지어내지 않고 그대로 넘겨 A의 검증이 "근거 출처 누락"으로 수동 확인 처리하게 한다.
                return result
            by_index: dict[int, dict] = {}
            for item in result.items:  # 같은 항목을 두 번 설명했으면 첫 번째만 쓴다
                if 0 <= item.breakdown_index < len(breakdown) and item.breakdown_index not in by_index:
                    by_index[item.breakdown_index] = item.model_dump()
            items = []
            for i, row in enumerate(breakdown):
                data = by_index.get(i) or {
                    # AI가 빠뜨린 항목은 규칙이 계산한 내용을 그대로 요약한다 (AI 설명이 아님을 표시)
                    "breakdown_index": i,
                    "explanation": f"[규칙 요약] {row.get('item')}: {row.get('points')}점"
                                   f" (반영률 {row.get('weight', 1)}). {row.get('note') or ''}".strip(),
                    "evidence": row.get("evidence") or [],
                }
                if row.get("evidence"):
                    data["evidence"] = row["evidence"]
                items.append(data)
            return schema.model_validate({"items": items})
        if prompt != "extract_facts":
            return result
        rules = load_rules()
        allowed = set(rules["group_a"]) | set(rules["group_b"])
        by_type = rules.get("allowed_signals_by_type") or {}
        per_type = by_type.get(payload.get("disclosure_type"))
        if per_type is not None:  # 이 공시 유형에서 올릴 수 있는 신호만 (유형에 안 맞는 신호는 AI가 만들 수 없다)
            allowed = allowed & set(per_type)
        kept = [f for f in result.facts if str(f.signal_type).strip().upper() in allowed]
        if len(kept) != len(result.facts):
            log.info("규칙표에 없거나 이 공시 유형에 맞지 않는 signal_type 제외: %s",
                     [f.signal_type for f in result.facts if f not in kept])
        chunks = payload.get("chunks") or []
        facts = []
        for f in kept:
            data = f.model_dump()
            for e in data["evidence"]:
                chunk, snapped = best_chunk(e, chunks)
                if chunk is not None:
                    # source·rcept_no·location은 검색 도구가 준 값이다. AI가 베끼다 틀리면(예: 마스킹된 접수번호) 코드가 바로잡는다.
                    e["source"], e["rcept_no"], e["location"] = chunk["source"], chunk["rcept_no"], chunk["location"]
                    e["quote"] = snapped
            facts.append(data)
        return schema.model_validate({"facts": facts})


class UnsafeDraftInjector:
    """시연용(FAKE_UNSAFE_DRAFT=true): 첫 안내문 초안에만 금지 표현을 덧붙인다. 다시 쓰기(rewrite_message)는 건드리지 않아
    가드레일이 잡고 → 다시 쓰고 → 통과하는 흐름이 trace에 남는다."""

    PHRASE = " 지금 바로 매도하세요."

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def generate(self, prompt, schema, payload):
        result = await self.inner.generate(prompt, schema, payload)
        if prompt == "draft_message":
            return schema.model_validate({"drafts": {c: t + self.PHRASE for c, t in result.drafts.items()}})
        return result


# ------------------------------------------------------------------ 관측 (LangSmith)
def enable_langsmith(settings) -> bool:
    """`.env`의 LangSmith 설정을 환경 변수로 내보낸다. LangChain·LangGraph는 환경 변수만 읽기 때문에
    이걸 하지 않으면 키가 있어도 trace가 전송되지 않는다. 테스트(pytest)에서는 전송하지 않는다."""
    if os.environ.get("PYTEST_CURRENT_TEST") or not (settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY):
        return False
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.LANGSMITH_API_KEY)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.LANGSMITH_PROJECT)
    return True


def make_classifier():
    """rules.yaml 제목 키워드 표 + 첨부만 올리는 정정 공시([첨부정정]·[첨부추가])는 해당 없음."""
    base = load_classifier(DEFAULT_PATH)
    skip = tuple(load_rules().get("skip_title_prefixes", []))

    def classify(title: str):
        if skip and title.lstrip().startswith(skip):
            return None
        return base(title)

    return classify


# ------------------------------------------------------------------ 조립
async def build_dependencies(settings=None, *, tools=None, model=None, harness=None) -> Dependencies:
    """tools·model·harness를 비우면 운영 구성(MCP 서버 2개, Anthropic/OpenAI 모델, D의 Harness)을 만든다."""
    settings = settings or get_settings()
    enable_langsmith(settings)
    harness = harness or default_harness()
    if tools is None:
        from agent.mcp_client import MCPTools

        tools = await MCPTools.connect(settings)
    if model is None:
        model = build_model(harness, settings)
    if settings.FAKE_UNSAFE_DRAFT:
        model = UnsafeDraftInjector(model)
    return Dependencies(
        tools=tools,
        model=KnownSignalsModel(model),
        harness=harness,
        persistence=AssessmentStore(tools, audit, harness, upsert_confirmed=True),
        classify=make_classifier(),
        signal_input=signal_input,
        create_task=ToolTaskCreator(tools, harness, authorize_task, idempotency_confirmed=True),
        quiet_types=frozenset(load_rules().get("no_signal_ok_types", [])),
    )
