"""모델이 읽기 전용 도구를 고르는 단계: 허용 범위·고정 인자·오류 처리."""
import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from agent.runtime import AgentRuntime
from agent.state import ToolSelection
from conftest import RCEPT


def selected_refs(kit):
    """모델이 고른 참고 문서 조회 (안내문 작성 노드가 코드로 부르는 '안내문가이드' 조회는 제외)."""
    return [a for n, a in kit.tools.calls if n == "search_reference" and a.get("category") != "안내문가이드"]


def run(kit):
    return asyncio.run(AgentRuntime(kit.deps, InMemorySaver()).start(RCEPT))


def test_model_cannot_pick_mutation_tools():
    for tool in ("create_contact_task", "save_assessment", "update_task_status"):
        with pytest.raises(ValidationError):
            ToolSelection.model_validate({"requests": [{"tool": tool, "query": "x"}]})


def test_no_request_means_no_extra_tool_call(kit):
    state = run(kit)
    assert state["status"] == "WAITING_APPROVAL"
    assert selected_refs(kit) == []
    assert [n for n, _ in kit.tools.calls].count("search_disclosure_text") == 1


def test_reference_lookup_reaches_explain_risk_only_as_context(kit):
    kit.model.tool_requests = [{"tool": "search_reference", "query": "감사의견 거절 의미", "category": "상장규정"}]
    state = run(kit)
    assert state["status"] == "WAITING_APPROVAL" and state["error"] is None
    ref_calls = selected_refs(kit)
    assert ref_calls and ref_calls[0]["category"] == "상장규정"
    explain_payloads = [p for name, p in kit.model.calls if name == "explain_risk"]
    assert explain_payloads and "references" in explain_payloads[0]
    assert "references" not in [p for name, p in kit.model.calls if name == "extract_facts"][0]


def test_disclosure_search_is_pinned_to_current_rcept_no(kit):
    kit.model.tool_requests = [{"tool": "search_disclosure_text", "query": "감사의견"}]
    run(kit)
    assert all(a["rcept_no"] == RCEPT for n, a in kit.tools.calls if n == "search_disclosure_text")


def test_invalid_category_is_not_called_and_flow_continues(kit):
    kit.model.tool_requests = [{"tool": "search_reference", "query": "무엇", "category": "없는분류"}]
    state = run(kit)
    assert state["status"] == "WAITING_APPROVAL"
    assert selected_refs(kit) == []


def test_tool_failure_during_selection_does_not_stop_the_run(kit):
    from shared.schemas import ToolResponse
    original = kit.tools.call

    async def call(name, arguments):
        if name == "search_reference" and arguments.get("category") != "안내문가이드":
            return ToolResponse.model_validate({"ok": False, "error": {"code": "DB_ERROR", "message": "x"}})
        return await original(name, arguments)

    kit.tools.call = call
    kit.model.tool_requests = [{"tool": "search_reference", "query": "용어", "category": "회계용어"}]
    state = run(kit)
    assert state["status"] == "WAITING_APPROVAL" and state["error"] is None


def select_payloads(kit):
    return [p for name, p in kit.model.calls if name == "select_tools"]


def test_second_round_sees_the_first_results_and_stops_when_empty(kit):
    kit.model.tool_rounds = {"explain_risk": [
        [{"tool": "search_reference", "query": "감사의견 거절", "category": "회계용어"}],
        [{"tool": "search_reference", "query": "상장폐지 사유", "category": "상장규정"}],
    ]}
    state = run(kit)
    assert state["status"] == "WAITING_APPROVAL" and state["error"] is None
    explain_rounds = [p for p in select_payloads(kit) if p["purpose"] == "explain_risk"]
    assert len(explain_rounds) == 2                       # 두 번째 선택은 첫 결과를 본 뒤에 이루어진다
    assert explain_rounds[0]["history"] == []
    assert "감사의견 거절" in explain_rounds[1]["history"][0] and "1건" in explain_rounds[1]["history"][0]
    assert len(selected_refs(kit)) == 2


def test_repeating_the_same_request_ends_the_loop(kit):
    kit.model.tool_requests = [{"tool": "search_reference", "query": "같은 질문", "category": "회계용어"}]  # 매 호출 같은 요청
    state = run(kit)
    assert state["status"] == "WAITING_APPROVAL"
    # 노드(extract_facts, explain_risk)마다 한 번씩만 실행된다: 같은 노드의 두 번째 요청은 되풀이라 실행하지 않는다
    assert len(selected_refs(kit)) == 2
    assert len([p for p in select_payloads(kit) if p["purpose"] == "explain_risk"]) == 2


def test_tool_rounds_are_capped_and_fit_the_turn_budget(kit):
    from agent.tool_loop import MAX_ROUNDS
    kit.model.tool_rounds = {p: [[{"tool": "search_reference", "query": f"{p}{i}", "category": "회계용어"}] for i in range(10)]
                             for p in ("extract_facts", "explain_risk")}
    state = run(kit)
    assert state["status"] == "WAITING_APPROVAL" and state["error"] is None
    for purpose in ("extract_facts", "explain_risk"):
        assert len([p for p in select_payloads(kit) if p["purpose"] == purpose]) <= MAX_ROUNDS
    assert len(kit.model.calls) <= 8                       # 모델 호출 8회 한도 안


def test_old_history_is_summarized_within_the_budget():
    """이력이 예산을 넘으면 오래된 조회 결과는 요약되고 최근 것은 그대로 남는다 (Harness 3: Context)."""
    from agent.tool_loop import HISTORY_BUDGET, _history_for_model
    from harness.context import estimate_tokens

    history = [{"role": "user", "content": f"search_reference(질문{i}) → 3건: " + ("가나다라 " * 600)} for i in range(5)]
    msgs = _history_for_model("explain_risk", history)
    assert msgs[0].startswith("[이전 대화 요약]") and "질문1" in msgs[0]
    assert msgs[-1] == history[-1]["content"] and msgs[-2] == history[-2]["content"]
    assert len(msgs) < len(history)
    short = [{"role": "user", "content": "search_reference(짧음) → 1건"}]
    assert _history_for_model("x", short) == [short[0]["content"]]  # 예산 안이면 그대로
