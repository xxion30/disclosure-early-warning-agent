async def save_rejection(state, deps):
    # The store audits decisions with an idempotency key, not a rejected task row.
    for cid, decision in state["decisions"].items():
        await deps.persistence.audit(state, "customer_decision", {
            **decision, "idempotency_key": f'{state["assessment_id"]}:{cid}:decision',
        })
    return {}
