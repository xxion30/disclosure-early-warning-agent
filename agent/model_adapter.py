"""모델 생성과 AI 출력 보정 (담당: R2): 기본·대체 모델, 구조화 출력, 규칙표에 없는 신호 걸러내기, 인용을 원문 그대로로 맞추기.

assembly.py에서 분리했다.
"""
from __future__ import annotations

import difflib
import logging

from risk_engine.loader import load_rules

log = logging.getLogger("agent.model_adapter")


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
