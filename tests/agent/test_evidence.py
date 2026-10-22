"""근거 검증: 원문에서 확인되지 않은 인용은 점수 계산에 닿지 않는다. (담당: R2)"""
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
        if failure == "bad_evidence":
            assert state["fact_attempts"] == 3
            assert state["retry_count"] == 2
    asyncio.run(scenario())
