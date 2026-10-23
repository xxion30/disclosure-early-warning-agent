async def classify(state, deps):
    if deps.phase1 is not None:
        from agent.phase1 import RiskRoute
        routes = [RiskRoute.model_validate(r).model_dump()
                  for r in deps.phase1.classify(state["disclosure"]["title"])]
        keys = [(r["bucket"], r["item"]) for r in routes]
        if len(keys) != len(set(keys)):
            raise ValueError("위험 항목 분류 중복")
        return {"risk_routes": routes, "relevant": bool(routes), "disclosure_type": "PHASE1",
                "status": "RUNNING", "flags": [], "phase1": True}
    kind = deps.classify(state["disclosure"]["title"])
    return {"disclosure_type": kind or "", "relevant": kind is not None,
            "status": "RUNNING" if kind is not None else "DONE"}
