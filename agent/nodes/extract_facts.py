from agent.evidence import chunks_from, validate_evidence
from agent.execution import call_tool, generate
from agent.ports import AgentError
from agent.state import FactsOutput
from agent.tool_loop import select_and_run


async def extract_facts(state, deps):
    attempt = state.get("fact_attempts", 0) + 1
    raw = await call_tool(deps, "search_disclosure_text", {
        "rcept_no": state["disclosure"]["rcept_no"],
        "query": f'{state["disclosure_type"]} 조건 값과 근거 문장', "k": 5,
    })
    try:
        chunks = chunks_from(raw)
        if any(c["rcept_no"] != state["disclosure"]["rcept_no"] for c in chunks):
            raise AgentError("다른 공시의 RAG 조각")
        if attempt == 1:
            # 모델이 원문에서 더 찾아볼 곳이 있으면 읽기 전용 도구를 골라 조회한다 (재시도 때는 생략)
            found = await select_and_run(deps, "extract_facts", state["disclosure"]["rcept_no"], {
                "disclosure_type": state["disclosure_type"],
                "retrieved": [{"location": c["location"], "preview": c["text"][:120]} for c in chunks],
            })
            seen = {(c["source"], c["location"]) for c in chunks}
            extra = [c for c in found["disclosure_chunks"]
                     if all(isinstance(c.get(k), str) and c[k].strip() for k in ("source", "text", "rcept_no", "location"))
                     and (c["source"], c["location"]) not in seen]
            chunks = chunks + extra[:3]
        result = await generate(deps, "extract_facts", FactsOutput, {
            "disclosure": state["disclosure"], "disclosure_type": state["disclosure_type"],
            "chunks": chunks, "attempt": attempt,
        })
        if not result.facts:
            if state["disclosure_type"] in deps.quiet_types:
                # 신호가 없는 공시: 평가·경보를 만들지 않고 끝낸다.
                return {"extracted_facts": {"facts": []}, "evidence": [], "no_signal": True, "status": "DONE",
                        "fact_attempts": attempt, "retry_count": attempt - 1}
            raise ValueError("추출된 사실 없음")
        evidence = [e.model_dump() for fact in result.facts for e in fact.evidence]
        validate_evidence(evidence, chunks)
        return {"extracted_facts": result.model_dump(), "evidence": evidence,
                "fact_attempts": attempt, "retry_count": attempt - 1}
    except (ValueError, AgentError) as exc:
        if isinstance(exc, AgentError) and exc.status == "FAILED":
            raise
        update = {"fact_attempts": attempt, "retry_count": attempt - 1}
        if attempt >= 3:
            update.update(error="사실·근거 검증 재시도 소진", status="MANUAL_CHECK")
        return update
