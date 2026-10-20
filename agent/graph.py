"""Deterministic orchestration; models cannot choose mutation tools or scores."""
from functools import partial

from langgraph.graph import END, START, StateGraph

from agent.execution import guarded
from agent.nodes.receive import receive
from agent.nodes.classify import classify
from agent.nodes.extract_facts import extract_facts
from agent.nodes.score_risk import score_risk
from agent.nodes.explain_risk import explain_risk
from agent.nodes.find_customers import find_customers
from agent.nodes.draft_messages import draft_messages
from agent.nodes.guardrail_check import guardrail_check
from agent.nodes.pb_approval import pb_approval, pending_customers
from agent.nodes.create_tasks import create_tasks
from agent.nodes.save_and_summarize import save_and_summarize
from agent.nodes.save_rejection import save_rejection
from agent.nodes.handle_failure import handle_failure
from agent.state import AgentState


def route_by_level(state):
    return "find_customers" if state["risk_level"] in ("High", "Critical") else "save_and_summarize"


def build_graph(deps, checkpointer):
    graph = StateGraph(AgentState)
    operations = {
        "receive": receive, "classify": classify, "extract_facts": extract_facts,
        "score_risk": score_risk, "explain_risk": explain_risk,
        "find_customers": find_customers, "draft_messages": draft_messages,
        "guardrail_check": guardrail_check, "pb_approval": pb_approval,
        "create_tasks": create_tasks, "save_rejection": save_rejection,
        "save_and_summarize": save_and_summarize,
    }

    async def prepare_approval(state):
        assessment_id = await deps.persistence.save(state, "WAITING_APPROVAL")
        await deps.harness.event("waiting_approval", {
            "trace_id": state["trace_id"], "assessment_id": assessment_id,
        })
        return {"assessment_id": assessment_id, "status": "WAITING_APPROVAL"}

    async def finalize(state):
        status = "WAITING_APPROVAL" if pending_customers(state) else "DONE"
        assessment_id = await deps.persistence.save(state, status)
        return {"assessment_id": assessment_id, "status": status}

    async def completed(state):
        await deps.harness.event("done", {"trace_id": state["trace_id"], "status": state["status"]})
        return {}

    for name, operation in operations.items():
        graph.add_node(name, guarded(name, partial(operation, deps=deps), deps))
    graph.add_node("prepare_approval", guarded("prepare_approval", prepare_approval, deps))
    graph.add_node("finalize", guarded("finalize", finalize, deps))
    graph.add_node("completed", guarded("completed", completed, deps))
    # Failure recording has its own bounded runtime path, even after the main budget expires.
    graph.add_node("handle_failure", partial(handle_failure, deps=deps))

    def edge(source, destination):
        graph.add_conditional_edges(source, lambda state: "handle_failure" if state.get("error") else destination,
                                    {"handle_failure": "handle_failure", destination: destination})

    def branch(source, router, destinations):
        graph.add_conditional_edges(source, lambda state: "handle_failure" if state.get("error") else router(state),
                                    {target: target for target in [*destinations, "handle_failure"]})

    graph.add_edge(START, "receive")
    edge("receive", "classify")
    branch("classify", lambda s: "extract_facts" if s["relevant"] else "completed", ["extract_facts", "completed"])
    branch("extract_facts", lambda s: "score_risk" if s.get("extracted_facts") else "extract_facts", ["score_risk", "extract_facts"])
    edge("score_risk", "explain_risk")
    branch("explain_risk", route_by_level, ["find_customers", "save_and_summarize"])
    edge("save_and_summarize", "completed")
    branch("find_customers", lambda s: "draft_messages" if pending_customers(s) else "save_and_summarize", ["draft_messages", "save_and_summarize"])
    edge("draft_messages", "guardrail_check")
    branch("guardrail_check", lambda s: "draft_messages" if s["rewrite_customers"] else "prepare_approval", ["draft_messages", "prepare_approval"])
    edge("prepare_approval", "pb_approval")
    edge("pb_approval", "save_rejection")
    edge("save_rejection", "create_tasks")
    branch("create_tasks", lambda s: "create_tasks" if any(
        d["status"] == "approved" and cid not in s["customer_tasks"] for cid, d in s["decisions"].items()
    ) else "finalize", ["create_tasks", "finalize"])
    branch("finalize", lambda s: "draft_messages" if s["rewrite_customers"] else (
        "prepare_approval" if pending_customers(s) else "completed"),
        ["draft_messages", "prepare_approval", "completed"])
    graph.add_edge("completed", END)
    graph.add_edge("handle_failure", END)
    return graph.compile(checkpointer=checkpointer)
