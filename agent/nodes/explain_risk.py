from agent.evidence import validate_known_evidence
from agent.execution import generate
from agent.ports import AgentError
from agent.public_risk import disclosure_context, public_risk_context, reference_context
from agent.state import ExplanationOutput
from agent.tool_loop import select_and_run
from harness.guardrails import check_explanation


async def explain_risk(state, deps):
    if state.get("phase1"):
        return await explain_phase1(state, deps)
    breakdown = state["score_result"]["breakdown"]
    # 규정·용어 설명이 필요하면 모델이 참고 문서를 직접 검색한다 (읽기 전용, 인용 근거는 점수 내역의 원문만 쓴다)
    found = await select_and_run(deps, "explain_risk", state["disclosure"]["rcept_no"], {
        "items": [{"index": i, "item": row.get("item") or row.get("signal_type")} for i, row in enumerate(breakdown)],
    })
    payload = {"disclosure": disclosure_context(state["disclosure"]),
               "score_result": public_risk_context(state["score_result"])}
    if found["references"]:
        payload["references"] = reference_context({"chunks": found["references"]})["chunks"]
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
        if check_explanation(item.explanation):
            raise AgentError("설명문 금지 표현·내부 점수 노출 확인 필요")
        lines.append(item.explanation + "\n" + "\n".join(
            f'“{e.quote}” ({e.source}, {e.rcept_no}, {e.location})' for e in item.evidence
        ))
    return {"ai_explanation": "\n\n".join(lines)}


async def explain_phase1(state, deps):
    """Bounded explanation retries; financial values and stages are rule outputs."""
    breakdown = state["score_result"]["breakdown"]
    if not breakdown:
        return {"ai_explanation": "확인 가능한 위험 항목이 없습니다.", "explanation_errors": []}
    attempt = state.get("explanation_attempts", 0) + 1
    try:
        # No customer names or full disclosure body. Numerical scores stay internal.
        result = await generate(deps, "explain_risk", ExplanationOutput, {
            "disclosure": disclosure_context(state["disclosure"]),
            "score_result": public_risk_context(state["score_result"]),
            "previous_errors": state.get("explanation_errors", []),
        })
        if sorted(i.breakdown_index for i in result.items) != list(range(len(breakdown))):
            raise ValueError("판정 항목별 설명 누락·중복")
        lines = []
        for item in sorted(result.items, key=lambda x: x.breakdown_index):
            validate_known_evidence(item.evidence, breakdown[item.breakdown_index]["evidence"])
            problems = check_explanation(item.explanation)
            if problems:
                raise ValueError(" / ".join(problems))
            lines.append(item.explanation + "\n" + "\n".join(
                f'“{e.quote}” ({e.source}, {e.rcept_no}, {e.location})' for e in item.evidence))
        return {"ai_explanation": "\n\n".join(lines), "explanation_attempts": attempt, "explanation_errors": []}
    except (ValueError, AgentError) as exc:
        if isinstance(exc, AgentError) and exc.status == "FAILED":
            raise
        update = {"explanation_attempts": attempt, "explanation_errors": ["설명 형식·근거·금지 표현 확인 필요"]}
        if attempt >= 3:
            update.update(error="설명 검증 재시도 소진", status="MANUAL_CHECK")
        return update
