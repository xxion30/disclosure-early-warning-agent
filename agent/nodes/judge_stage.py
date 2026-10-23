from agent.evidence import retained_data, validate_evidence, validate_known_evidence
from agent.nodes.phase1_common import rule_call
from agent.phase1 import StageResult
from agent.ports import AgentError


async def judge_stage(state, deps):
    raw = await rule_call(deps, "judge_stage", {
        "disclosure": state["disclosure"], "red": state.get("red_result", {}),
        "orange": state.get("orange_result", {}), "yellow": state.get("yellow_result", {}),
    })
    result = StageResult.model_validate(raw)
    if result.needs_manual_check:
        raise AgentError("규칙 도구가 수동 확인을 요청했습니다")
    known = list(state["evidence"])
    for row in result.breakdown:
        evidence = row.get("evidence") or []
        if not evidence:
            raise AgentError("판정 항목의 근거 출처 누락")
        if row.get("source_chunks"):
            validate_evidence(evidence, row["source_chunks"])
        else:
            validate_known_evidence(evidence, known)
        known.extend(evidence)
    if result.stage != "NONE" and not result.breakdown:
        raise AgentError("단계 판정의 근거 내역이 없습니다")
    return {"stage": result.stage, "orange_score": result.orange_score,
            "yellow_score": result.yellow_score, "combined_ratio": result.combined_ratio,
            "score_result": retained_data(result.model_dump()), "signals": retained_data(result.signals),
            "evidence": retained_data(known),
            "red_result": retained_data(state.get('red_result', {})),
            "orange_result": retained_data(state.get('orange_result', {})),
            "yellow_result": retained_data(state.get('yellow_result', {}))}
