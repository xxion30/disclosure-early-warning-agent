from datetime import datetime, timezone

from langgraph.types import interrupt

from agent.nodes.guardrail_check import message_flags
from agent.state import ApprovalBatch, PBContext


def pending_customers(state):
    return [c["customer_id"] for c in state["affected_customers"]
            if c["priority"] in ("P1", "P2") and c["customer_id"] not in state["decisions"]]


async def validate_batch(state, batch, pb, deps):
    pending = set(pending_customers(state))
    for decision in batch.decisions:
        cid = decision.customer_id
        if cid not in pb.allowed_customer_ids:
            raise PermissionError("담당 고객만 결정할 수 있습니다")
        if cid not in pending:
            raise ValueError("미결정 대상 고객이 아닙니다")
        if decision.status == "approved":
            message = decision.edited_message if decision.edited_message is not None else state["drafts"].get(cid, "")
            if await message_flags(message, deps):
                raise ValueError("가드레일을 통과한 최종 문안이 필요합니다")


async def pb_approval(state, deps):
    # No writes before interrupt: this node is replayed on resume.
    pending = pending_customers(state)
    raw = interrupt({
        "rcept_no": state["disclosure"]["rcept_no"],
        "assessment_id": state["assessment_id"], "pending_customers": pending,
        "drafts": {cid: state["drafts"].get(cid, "") for cid in pending},
        "manual_drafts": state.get("manual_drafts", []),
        "guardrail_flags": state.get("guardrail_flags", []),
    })
    pb = PBContext.model_validate(raw["verified_pb"])
    batch = ApprovalBatch.model_validate(raw["batch"])
    await validate_batch(state, batch, pb, deps)
    decisions, drafts = dict(state["decisions"]), dict(state["drafts"])
    attempts = dict(state["draft_attempts"])
    manual, rewrite = set(state.get("manual_drafts", [])), []
    now = datetime.now(timezone.utc).isoformat()
    for item in batch.decisions:
        cid = item.customer_id
        if item.status == "rewrite":
            rewrite.append(cid)
            manual.discard(cid)
            attempts[cid] = 0
            continue
        decisions[cid] = {**item.model_dump(), "pb_id": pb.pb_id, "decided_at": now}
        if item.edited_message is not None:
            drafts[cid] = item.edited_message
        manual.discard(cid)
    return {"decisions": decisions, "drafts": drafts,
            "approval": {"status": "PARTIAL", "pb_id": pb.pb_id,
                         "edited_drafts": {i.customer_id: i.edited_message for i in batch.decisions
                                           if i.edited_message is not None},
                         "reject_reason": {i.customer_id: i.reject_reason for i in batch.decisions
                                           if i.status == "rejected"}, "decided_at": now},
            "rewrite_customers": rewrite, "manual_drafts": sorted(manual),
            "draft_attempts": attempts, "status": "RUNNING"}
