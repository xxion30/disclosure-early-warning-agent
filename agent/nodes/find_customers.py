from agent.execution import call_tool
from agent.ports import AgentError
from shared.schemas import AffectedCustomer


async def find_customers(state, deps):
    """해당 종목을 보유한 고객 전원을 고른다. 영향도·우선순위는 쓰지 않는다 (순서: 보유 평가금액 큰 순)."""
    data = await call_tool(deps, "get_holders", {"stock_code": state["disclosure"]["stock_code"]})
    items = data.get("holders") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise AgentError("보유 고객 응답 형식 불일치")
    customers = [AffectedCustomer.model_validate(
        {"customer_id": i.get("customer_id"), "eval_amount": i.get("eval_amount")}).model_dump() for i in items]
    if len({c["customer_id"] for c in customers}) != len(customers):
        raise AgentError("보유 고객 중복")
    customers.sort(key=lambda c: (-c["eval_amount"], c["customer_id"]))
    return {"affected_customers": customers}
