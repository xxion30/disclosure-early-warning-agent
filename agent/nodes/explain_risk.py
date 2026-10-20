from agent.evidence import validate_known_evidence
from agent.execution import generate
from agent.ports import AgentError
from agent.state import ExplanationOutput
from agent.tool_loop import select_and_run


async def explain_risk(state, deps):
    breakdown = state["score_result"]["breakdown"]
    # 규정·용어 설명이 필요하면 모델이 참고 문서를 직접 검색한다 (읽기 전용, 인용 근거는 점수 내역의 원문만 쓴다)
    found = await select_and_run(deps, "explain_risk", state["disclosure"]["rcept_no"], {
        "items": [{"index": i, "item": row.get("item") or row.get("signal_type")} for i, row in enumerate(breakdown)],
    })
    payload = {"disclosure": state["disclosure"], "score_result": state["score_result"],
               "evidence": state["evidence"]}
    if found["references"]:
        payload["references"] = found["references"]
    result = await generate(deps, "explain_risk", ExplanationOutput, payload)
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
