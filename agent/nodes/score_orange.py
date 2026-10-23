from agent.nodes.phase1_common import routes_for, rule_call


async def score_orange(state, deps):
    routes = routes_for(state, "ORANGE")
    if not routes:
        return {"orange_result": {}}
    result = await rule_call(deps, "score_orange", {
        "disclosure": state["disclosure"], "routes": routes, "flags": state.get("flags", []),
    })
    return {"orange_result": result}
