from agent.evidence import chunks_from, validate_evidence
from agent.execution import call_tool, generate
from agent.ports import AgentError
from agent.state import FactsOutput


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
        result = await generate(deps, "extract_facts", FactsOutput, {
            "disclosure": state["disclosure"], "disclosure_type": state["disclosure_type"],
            "chunks": chunks, "attempt": attempt,
        })
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
