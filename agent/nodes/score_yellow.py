from agent.nodes.phase1_common import routes_for, rule_call


async def score_yellow(state, deps):
    routes = routes_for(state, "YELLOW")
    if not routes:
        return {"yellow_result": {}}
    result = await rule_call(deps, "score_yellow", {
        "disclosure": state["disclosure"], "routes": routes,
        "snapshot": state["financial_snapshot"],
    })
    return {"yellow_result": result}
