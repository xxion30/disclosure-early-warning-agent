from agent.evidence import retained_data, validate_evidence, validate_known_evidence
from agent.execution import call_tool
from agent.ports import AgentError
from shared.schemas import ScoreResult, Signal


async def score_risk(state, deps):
    disclosure = state["disclosure"]
    # History is available to B's calculator; never append it to new_signals again.
    await call_tool(deps, "get_signal_history", {"corp_code": disclosure["corp_code"], "months": 12})
    new_signals = deps.signal_input(state["extracted_facts"])
    if not new_signals:
        raise AgentError("규칙 계산에 전달할 검증된 사실 없음")
    raw = await call_tool(deps, "calculate_risk_score", {
        "corp_code": disclosure["corp_code"], "new_signals": new_signals,
        "as_of": disclosure["filed_at"],
    })
    score = ScoreResult.model_validate(raw)
    # B must expose calculated new signals, or bind an adapter producing this field.
    if "signals" not in raw or not score.breakdown:
        raise AgentError("계산 응답에 signals·breakdown 계약 필요")
    signals = [Signal.model_validate(item).model_dump() for item in raw["signals"]]
    known = list(state["evidence"])
    for item in score.breakdown:
        # Optional score-source chunks allow historical/base score citations.
        evidence = item.get("evidence", [])
        if evidence:
            if item.get("source_chunks"):
                validate_evidence(evidence, item["source_chunks"])
            else:
                validate_known_evidence(evidence, known)
            known.extend(evidence)
    return {"score_result": retained_data(score.model_dump()), "signals": retained_data(signals),
            "total_score": score.total_score, "risk_level": score.level,
            "combo_upgraded": score.combo_upgraded, "evidence": known}
