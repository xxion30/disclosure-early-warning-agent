from pydantic import ValidationError

from agent.execution import call_tool, generate
from agent.state import DraftsOutput


async def draft_messages(state, deps):
    drafts = dict(state["drafts"])
    attempts = dict(state.get("draft_attempts", {}))
    manual = set(state.get("manual_drafts", []))
    rewrite = set(state.get("rewrite_customers", []))
    customers = [c for c in state["affected_customers"] if c["priority"] in ("P1", "P2")
                 and c["customer_id"] not in state["decisions"]
                 and c["customer_id"] not in manual
                 and (c["customer_id"] not in drafts or c["customer_id"] in rewrite)]
    if not customers:
        return {}
    ids = {c["customer_id"] for c in customers}
    reference = await call_tool(deps, "search_reference", {
        "query": "공시 위험 안내문 작성 원칙", "category": "안내문가이드", "k": 5,
    })
    for cid in ids:
        attempts[cid] = attempts.get(cid, 0) + 1
    try:
        result = await generate(deps, "rewrite_message" if rewrite else "draft_message",
                                DraftsOutput, {
            "disclosure": state["disclosure"], "customers": customers,
            "score_result": state["score_result"], "ai_explanation": state["ai_explanation"],
            "reference": reference, "previous_drafts": {c: drafts.get(c) for c in ids},
            "flags": state.get("guardrail_flags", []),
        })
        if set(result.drafts) != ids:
            raise ValueError("초안 고객 집합 불일치")
        drafts.update(result.drafts)
    except (ValidationError, ValueError):
        # Validation failures use the same bounded rewrite loop as unsafe messages.
        drafts.update({cid: "" for cid in ids})
    return {"drafts": drafts, "draft_attempts": attempts,
            "rewrite_customers": [], "retry_count": max(attempts.values()) - 1}
