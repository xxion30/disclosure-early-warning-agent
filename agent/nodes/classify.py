async def classify(state, deps):
    kind = deps.classify(state["disclosure"]["title"])
    return {"disclosure_type": kind or "", "relevant": kind is not None,
            "status": "RUNNING" if kind is not None else "DONE"}
