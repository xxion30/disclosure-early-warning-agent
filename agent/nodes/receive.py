from agent.execution import call_tool
from agent.ports import AgentError


async def receive(state, deps):
    rcept_no = state["disclosure"]["rcept_no"]
    disclosure = await call_tool(deps, "get_disclosure", {"rcept_no": rcept_no})
    required = ("rcept_no", "corp_code", "title", "filed_at")
    if not isinstance(disclosure, dict) or any(not disclosure.get(key) for key in required):
        raise AgentError("공시 필수 필드 누락")
    if disclosure["rcept_no"] != rcept_no:
        raise AgentError("조회 공시 접수번호 불일치")
    profile = await call_tool(deps, "get_company_profile", {"corp_code": disclosure["corp_code"]})
    if not isinstance(profile, dict) or not profile.get("stock_code"):
        raise AgentError("회사 종목코드 누락")
    # The full disclosure body is deliberately not checkpointed or sent to the model.
    data = {**{key: disclosure[key] for key in required},
            "stock_code": profile["stock_code"], "corp_name": profile.get("name", "")}
    if deps.phase1 is not None and isinstance(disclosure.get("structured_data"), dict):
        data["structured_data"] = disclosure["structured_data"]
    return {"disclosure": data}
