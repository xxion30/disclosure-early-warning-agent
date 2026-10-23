"""Only textual conditions are inferred by the LLM; numeric API fields stay data."""
from pydantic import ValidationError

from agent.evidence import chunks_from, select_chunks, validate_evidence
from agent.execution import call_tool, generate
from agent.nodes.phase1_common import routes_for
from agent.phase1 import FlagsOutput
from agent.ports import AgentError
from agent.retrieval import RAGContractError
from agent.public_risk import disclosure_context
from agent.tool_loop import select_and_run


async def extract_flags(state, deps):
    from shared.rule_catalog import load_catalog
    numeric_fields = load_catalog()['numeric_fields'] if deps.unified is not None else {}
    routes = [r for r in state.get('risk_routes', []) if r['text_flags'] or numeric_fields.get(r['item'])]
    if not routes:
        return {"flags": [], "flag_attempts": 0, "flag_extraction_complete": True}
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
            "numeric_fields": {r['item']: numeric_fields.get(r['item'], []) for r in routes},
        })
        required = {(r["item"], flag) for r in routes for flag in r["text_flags"]}
        if {(f.item, f.flag) for f in result.flags} != required:
            raise ValueError("요청한 플래그 누락·추가")
        evidence = [e.model_dump() for f in result.flags for e in f.evidence]
        import re
        from math import isfinite
        seen = set()
        for fact in result.numeric_facts:
            key = (fact.item, fact.field)
            if (fact.field not in numeric_fields.get(fact.item, []) or fact.item not in {r['item'] for r in routes}
                    or key in seen or fact.unit != '%' or not isfinite(fact.value) or fact.value < 0):
                raise ValueError('요청하지 않거나 유효하지 않은 수치 사실')
            seen.add(key)
            quoted = [float(token.replace(',', '')) for ev in fact.evidence
                      for token in re.findall(r'(?<![\d.])([\d,]+(?:\.\d+)?)\s*%', ev.quote)]
            if not any(abs(value - fact.value) < 1e-8 for value in quoted):
                raise ValueError('수치와 % 단위가 인용문에 직접 나타나지 않음')
            evidence.extend(e.model_dump() for e in fact.evidence)
        validate_evidence(evidence, chunks)
        if any(f.value is None for f in result.flags):
            raise ValueError("본문 조건 확인 불가")
        sources = [{"source": c["source"], "rcept_no": c["rcept_no"], "location": c["location"],
                    **{k: c[k] for k in ("content_sha256", "normalization_version") if c.get(k)}}
                   for c in chunks]
        return {"flags": result.model_dump()["flags"], "evidence": [*state["evidence"], *evidence],
                "numeric_facts": result.model_dump()['numeric_facts'],
                "flag_extraction_complete": True,
                "verified_sources": sources,
                "flag_attempts": attempt, "retry_count": attempt - 1}
    except (ValidationError, ValueError, AgentError) as exc:
        if isinstance(exc, RAGContractError):
            raise
        if isinstance(exc, AgentError) and exc.status == "FAILED":
            raise
        update = {"flag_attempts": attempt, "retry_count": attempt - 1, "flag_extraction_complete": False}
        if attempt >= 3:
            update.update(error="텍스트 조건·근거 검증 재시도 소진", status="MANUAL_CHECK")
        return update
