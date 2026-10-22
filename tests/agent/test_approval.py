import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.runtime import AgentRuntime
from agent.state import ApprovalBatch, PBContext
from conftest import RCEPT

P01 = PBContext(pb_id="P01", allowed_customer_ids={"C001", "C003", "C005", "C007"})
P02 = PBContext(pb_id="P02", allowed_customer_ids={"C002", "C004", "C006"})


def approve(ids, edit=None):
    return ApprovalBatch(decisions=[{"customer_id": cid, "status": "approved",
                                    "edited_message": edit if cid == "C001" else None} for cid in ids])


def test_partial_approval_edit_rejection_and_duplicate(kit):
    async def scenario():
        runtime = AgentRuntime(kit.deps, InMemorySaver())
        await runtime.start(RCEPT)
        assert not kit.store.tasks
        edit = "{고객명}님, 공시 원문을 확인한 후 상담 부탁드립니다."
        first = approve(sorted(P01.allowed_customer_ids), edit)
        state = await runtime.resume(RCEPT, first, P01)
        assert state["error"] is None, state
        assert state["status"] == "WAITING_APPROVAL"
        assert len(kit.store.tasks) == 4
        assert len(state["task_ids"]) == 4
        assert state["drafts"]["C001"] == edit
        duplicate = await runtime.resume(RCEPT, first, P01)
        assert duplicate == state
        assert len(kit.store.create_attempts) == 4
        review = await runtime.get_review(RCEPT, P01)
        assert set(review["drafts"]) == P01.allowed_customer_ids
        with pytest.raises(PermissionError):
            await runtime.resume(RCEPT, approve(["C006"]), P01)
        final = await runtime.resume(RCEPT, ApprovalBatch(decisions=[
            {"customer_id": "C002", "status": "rejected", "reject_reason": "개별 검토"},
            {"customer_id": "C004", "status": "approved"},
            {"customer_id": "C006", "status": "approved"},
        ]), P02)
        assert final["error"] is None, final
        assert final["status"] == "DONE"
        assert len(kit.store.tasks) == 6
        assert final["tool_call_counts"]["create_contact_task"] == 6
        assert "1:C002" not in kit.store.tasks
        assert kit.store.audits["1:C002:decision"][1]["reject_reason"] == "개별 검토"
    asyncio.run(scenario())


def test_bad_edit_does_not_consume_interrupt(kit):
    async def scenario():
        runtime = AgentRuntime(kit.deps, InMemorySaver())
        before = await runtime.start(RCEPT)
        with pytest.raises(ValueError):
            await runtime.resume(RCEPT, approve(["C001"], "{고객명} 매도하세요"), P01)
        assert await runtime.get_state(RCEPT) == before
        after = await runtime.resume(RCEPT, approve(["C001"]), P01)
        assert len(after["task_ids"]) == 1
    asyncio.run(scenario())


def test_rewrite_request_does_not_create_task(kit):
    async def scenario():
        runtime = AgentRuntime(kit.deps, InMemorySaver())
        await runtime.start(RCEPT)
        state = await runtime.resume(RCEPT, ApprovalBatch(decisions=[
            {"customer_id": "C001", "status": "rewrite"},
        ]), P01)
        assert state["error"] is None, state
        assert state["status"] == "WAITING_APPROVAL"
        assert not state["decisions"]
        assert not kit.store.tasks
        assert kit.model.calls[-1][0] == "rewrite_message"
    asyncio.run(scenario())


def test_reject_reason_required():
    with pytest.raises(ValueError):
        ApprovalBatch(decisions=[{"customer_id": "C001", "status": "rejected"}])
