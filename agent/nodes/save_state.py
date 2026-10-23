"""Store a Phase 1 assessment before contact handling; R4 owns the actual store."""
from agent.ports import AgentError


async def save_state(state, deps):
    assessment_id = await deps.persistence.save(state, "RUNNING")
    if not isinstance(assessment_id, int) or isinstance(assessment_id, bool) or assessment_id <= 0:
        raise AgentError("평가 저장 ID 형식 불일치")
    return {"assessment_id": assessment_id}
