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
