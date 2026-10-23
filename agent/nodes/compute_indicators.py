"""Obtain code-calculated R3 indicators via the R4 binding; do not recalculate."""
from agent.nodes.phase1_common import routes_for, rule_call
from agent.ports import AgentError


async def compute_indicators(state, deps):
    if not routes_for(state, "YELLOW"):
        return {"financial_snapshot": {}}
    snapshot = await rule_call(deps, "financial_snapshot", {"disclosure": state["disclosure"]})
    if not isinstance(snapshot.get("indicators"), dict) or not snapshot["indicators"]:
        raise AgentError("재무 지표가 없습니다. 수집·계산 상태를 확인하세요")
    if any(value is None or value == "" for value in snapshot["indicators"].values()):
        raise AgentError("누락된 재무 지표: 0 또는 정상으로 대체할 수 없습니다")
    if not snapshot.get("evidence"):
        raise AgentError("재무 지표 출처가 없습니다")
    return {"financial_snapshot": snapshot}
