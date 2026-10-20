async def save_and_summarize(state, deps):
    assessment_id = await deps.persistence.save(state, "DONE")
    return {"assessment_id": assessment_id, "status": "DONE"}
