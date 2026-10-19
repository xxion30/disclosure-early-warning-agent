"""Harness 3 — Context: 토큰 예산, 오래된 메시지 요약, 도구 결과 자르기."""
import json
from typing import Any, Callable

TOOL_RESULT_MAX_CHARS = 4000
TOKEN_BUDGET = 6000
RAG_TOP_K = 5
KEEP_RECENT = 2  # 요약하지 않고 남기는 최근 메시지 수


def truncate_tool_result(result: Any, max_chars: int = TOOL_RESULT_MAX_CHARS) -> str:
    """4,000자를 넘으면 자르고 '(이하 생략, 전체 N자)'를 붙인다."""
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n(이하 생략, 전체 {len(text)}자)"


def estimate_tokens(text: str) -> int:
    """대략치. 한글은 글자당 약 1토큰, 그 외는 4글자당 1토큰으로 센다 (정확한 값은 LangSmith에서)."""
    korean = sum(1 for ch in text if "가" <= ch <= "힣")
    return korean + (len(text) - korean + 3) // 4


def _content(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def messages_tokens(messages: list) -> int:
    return sum(estimate_tokens(_content(m)) for m in messages)


def trim_messages(
    messages: list,
    summarize: Callable[[list], str],
    budget: int = TOKEN_BUDGET,
) -> list:
    """입력 토큰이 budget을 넘으면 오래된 메시지를 summarize()로 요약해 한 개로 줄인다.

    첫 메시지(시스템 지침)와 최근 KEEP_RECENT개는 그대로 둔다. summarize는 호출하는 쪽이
    LLM으로 만든다 (메시지 리스트 → 요약 문자열). 이미 예산 안이면 그대로 돌려준다.
    """
    if messages_tokens(messages) <= budget or len(messages) <= KEEP_RECENT + 1:
        return messages
    head, middle, tail = messages[:1], messages[1:-KEEP_RECENT], messages[-KEEP_RECENT:]
    summary = {"role": "user", "content": f"[이전 대화 요약]\n{summarize(middle)}"}
    return [*head, summary, *tail]


def top_k(chunks: list, k: int = RAG_TOP_K) -> list:
    """RAG 결과는 상위 k개 조각만 넣는다."""
    return chunks[:k]
