import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.runtime import AgentRuntime
from conftest import RCEPT


@pytest.mark.parametrize("failure", ["bad_evidence", "wrong_disclosure"])
def test_unverified_evidence_cannot_reach_scoring(kit, failure):
    async def scenario():
        if failure == "bad_evidence":
            kit.model.bad_evidence = True
        else:
            kit.tools.bad_chunks = True
        state = await AgentRuntime(kit.deps, InMemorySaver()).start(RCEPT)
        assert state["status"] == "MANUAL_CHECK"
        assert "calculate_risk_score" not in [name for name, _ in kit.tools.calls]
        assert not kit.store.tasks
        if failure == "bad_evidence":
            assert state["fact_attempts"] == 3
            assert state["retry_count"] == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["unsafe", "invalid_drafts"])
def test_rewrite_exhaustion_requires_manual_message(kit, failure):
    async def scenario():
        setattr(kit.model, failure, True)
        state = await AgentRuntime(kit.deps, InMemorySaver()).start(RCEPT)
        assert state["error"] is None, state
        assert state["status"] == "WAITING_APPROVAL"
        assert len(state["manual_drafts"]) == 7
        assert set(state["drafts"].values()) == {""}
        assert set(state["draft_attempts"].values()) == {3}
        assert [p for p, _ in kit.model.calls].count("rewrite_message") == 2
        assert not kit.store.tasks
    asyncio.run(scenario())
