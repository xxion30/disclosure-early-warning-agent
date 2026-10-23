from agent.execution import call_tool
from agent.ports import AgentError
from agent.structured_facts import validate_facts


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
        from agent.evidence import retained_data
        data["structured_data"] = retained_data(disclosure["structured_data"])
    if deps.unified is not None:
        from datetime import date
        context = state['execution_context']
        if date.fromisoformat(data['filed_at'][:10]) > date.fromisoformat(context['as_of']):
            raise AgentError('기준일 이후 공시를 분석할 수 없습니다')
        raw = await call_tool(deps, 'get_disclosure_facts', {'rcept_no': rcept_no})
        if not isinstance(raw, dict) or 'availability' not in raw:
            raise AgentError('get_disclosure_facts 중첩 응답 계약 불일치')
        facts = validate_facts(raw, data)
        actual_version = facts.get('facts_hash')
        expected_version = context.get('facts_version')
        if not expected_version:
            raise AgentError('실행 context의 facts_version 누락: R4 조회 해시 주입 필요')
        if actual_version is None or actual_version != expected_version:
            raise AgentError('구조화 자료 버전 불일치: 새 판정 필요')
        return {'disclosure': data, 'structured_facts': facts}
    return {"disclosure": data}
