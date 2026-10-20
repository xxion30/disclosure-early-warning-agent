from agent.execution import call_tool
from agent.ports import AgentError
from shared.schemas import CustomerImpact


async def find_customers(state, deps):
    data = await call_tool(deps, "compute_customer_impact", {
        "stock_code": state["disclosure"]["stock_code"], "level": state["risk_level"],
    })
    items = data.get("customers") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise AgentError("고객 영향도 응답 형식 불일치")
    customers = [CustomerImpact.model_validate(item).model_dump() for item in items]
    if len({c["customer_id"] for c in customers}) != len(customers):
        raise AgentError("영향 고객 중복")
    customers.sort(key=lambda c: (c["priority"], -c["impact_score"], c["customer_id"]))
    return {"affected_customers": customers}
