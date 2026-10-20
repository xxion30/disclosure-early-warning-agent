from agent.evidence import validate_known_evidence
from agent.execution import generate
from agent.ports import AgentError
from agent.state import ExplanationOutput


async def explain_risk(state, deps):
    breakdown = state["score_result"]["breakdown"]
    result = await generate(deps, "explain_risk", ExplanationOutput, {
        "disclosure": state["disclosure"], "score_result": state["score_result"],
        "evidence": state["evidence"],
    })
    indices = [item.breakdown_index for item in result.items]
    if sorted(indices) != list(range(len(breakdown))):
        raise AgentError("점수 내역별 설명 누락·중복")
    lines = []
    for item in sorted(result.items, key=lambda item: item.breakdown_index):
        # Each breakdown must declare its own evidence; current-disclosure evidence
        # is not silently reused for unrelated historical/base-score components.
        allowed = breakdown[item.breakdown_index].get("evidence", [])
        if not allowed:
            raise AgentError("점수 항목의 근거 출처 누락")
        validate_known_evidence(item.evidence, allowed)
        lines.append(item.explanation + "\n" + "\n".join(
            f'“{e.quote}” ({e.source}, {e.rcept_no}, {e.location})' for e in item.evidence
        ))
    return {"ai_explanation": "\n\n".join(lines)}
