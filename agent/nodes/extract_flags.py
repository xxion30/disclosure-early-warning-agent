"""Only textual conditions are inferred by the LLM; numeric API fields stay data."""
from pydantic import ValidationError

from agent.evidence import chunks_from, select_chunks, validate_evidence
from agent.execution import call_tool, generate
from agent.nodes.phase1_common import routes_for
from agent.phase1 import FlagsOutput
from agent.ports import AgentError
from agent.public_risk import disclosure_context
from agent.tool_loop import select_and_run


async def extract_flags(state, deps):
    routes = [r for r in state.get('risk_routes', []) if r['text_flags']]
    if not routes:
        return {"flags": [], "flag_attempts": 0}
    attempt = state.get("flag_attempts", 0) + 1
    receipt = state["disclosure"]["rcept_no"]
    query = " ".join(r["item"] for r in routes) + " 사유 목적 조건 근거"
    raw = await call_tool(deps, "search_disclosure_text", {"rcept_no": receipt, "query": query, "k": 5})
    try:
        chunks = chunks_from(raw)
        if deps.unified is not None:
            digest = raw.get('content_sha256') if isinstance(raw, dict) else None
            version = raw.get('normalization_version') if isinstance(raw, dict) else None
            if not digest or not version:
                raise AgentError('문서 해시·정규화 버전 누락')
            chunks = [{**c, 'content_sha256': digest, 'normalization_version': version} for c in chunks]
        if any(c["rcept_no"] != receipt for c in chunks):
            raise ValueError("다른 공시 조각")
        if attempt == 1:
            found = await select_and_run(deps, "extract_flags", receipt, {
                "routes": routes,
                "retrieved": [{"location": c["location"], "preview": c["text"][:120]} for c in chunks],
            })
            seen = {(c["source"], c["location"]) for c in chunks}
            for c in found["disclosure_chunks"]:
                if deps.unified is not None and (c.get('content_sha256') != digest or
                                                  c.get('normalization_version') != version):
                    raise AgentError('검색 중 원문·정규화 버전 변경')
                if c.get("rcept_no") == receipt and (c.get("source"), c.get("location")) not in seen:
                    chunks.extend(chunks_from([c]))
                    seen.add((c["source"], c["location"]))
        chunks = select_chunks(chunks)
        result = await generate(deps, "extract_flags", FlagsOutput, {
            "disclosure": disclosure_context(state["disclosure"]), "routes": routes, "chunks": chunks, "attempt": attempt,
        })
        required = {(r["item"], flag) for r in routes for flag in r["text_flags"]}
        if {(f.item, f.flag) for f in result.flags} != required:
            raise ValueError("요청한 플래그 누락·추가")
        evidence = [e.model_dump() for f in result.flags for e in f.evidence]
        validate_evidence(evidence, chunks)
        if any(f.value is None for f in result.flags):
            raise ValueError("본문 조건 확인 불가")
        sources = [{"source": c["source"], "rcept_no": c["rcept_no"], "location": c["location"],
                    **{k: c[k] for k in ("content_sha256", "normalization_version") if c.get(k)}}
                   for c in chunks]
        return {"flags": result.model_dump()["flags"], "evidence": [*state["evidence"], *evidence],
                "verified_sources": sources,
                "flag_attempts": attempt, "retry_count": attempt - 1}
    except (ValidationError, ValueError, AgentError) as exc:
        if isinstance(exc, AgentError) and exc.status == "FAILED":
            raise
        update = {"flag_attempts": attempt, "retry_count": attempt - 1}
        if attempt >= 3:
            update.update(error="텍스트 조건·근거 검증 재시도 소진", status="MANUAL_CHECK")
        return update
