from agent.nodes.phase1_common import routes_for, rule_call


async def mark_red(state, deps):
    routes = routes_for(state, "RED")
    if not routes:
        return {"red_result": {}}
    result = await rule_call(deps, "mark_red", {"disclosure": state["disclosure"], "routes": routes})
    return {"red_result": result}
