"""Phase 1 orchestration; activated only with explicit R4 bindings."""
from functools import partial

from langgraph.graph import END, START, StateGraph

from agent.execution import guarded
from agent.nodes.classify import classify
from agent.nodes.compute_indicators import compute_indicators
from agent.nodes.create_tasks import create_tasks
from agent.nodes.draft_messages import draft_messages
from agent.nodes.explain_risk import explain_risk
from agent.nodes.extract_flags import extract_flags
from agent.nodes.find_customers import find_customers
from agent.nodes.guardrail_check import guardrail_check
from agent.nodes.handle_failure import handle_failure
from agent.nodes.judge_stage import judge_stage
from agent.nodes.mark_red import mark_red
from agent.nodes.pb_approval import pb_approval, pending_customers
from agent.nodes.receive import receive
from agent.nodes.save_and_summarize import save_and_summarize
from agent.nodes.save_rejection import save_rejection
from agent.nodes.save_state import save_state
from agent.nodes.score_orange import score_orange
from agent.nodes.score_yellow import score_yellow
from agent.nodes.calculate_unified import calculate_unified
from agent.phase1 import ALERT_STAGES
from agent.state import AgentState


def build_phase1_graph(deps, checkpointer):
    graph = StateGraph(AgentState)
    operations = {
        "receive": receive, "classify": classify, "mark_red": mark_red,
        "extract_flags": extract_flags, "score_orange": score_orange,
        "compute_indicators": compute_indicators, "score_yellow": score_yellow,
        "judge_stage": judge_stage, "explain_risk": explain_risk, "save_state": save_state,
        "find_customers": find_customers, "draft_messages": draft_messages,
        "guardrail_check": guardrail_check, "pb_approval": pb_approval,
        "save_rejection": save_rejection, "create_tasks": create_tasks,
        "save_and_summarize": save_and_summarize,
    }
    if deps.unified is not None:
        for name in ('mark_red', 'score_orange', 'compute_indicators', 'score_yellow', 'judge_stage'):
            operations.pop(name)
        operations['calculate_unified'] = calculate_unified

    async def prepare_approval(state):
        aid = await deps.persistence.save(state, "WAITING_APPROVAL")
        await deps.harness.event("waiting_approval", {"trace_id": state["trace_id"], "assessment_id": aid})
        return {"assessment_id": aid, "status": "WAITING_APPROVAL"}

    async def finalize(state):
        status = "WAITING_APPROVAL" if pending_customers(state) else "DONE"
        aid = await deps.persistence.save(state, status)
        return {"assessment_id": aid, "status": status}

    async def completed(state):
        await deps.harness.event("done", {"trace_id": state["trace_id"], "status": state["status"]})
        return {}

    operations.update(prepare_approval=prepare_approval, finalize=finalize, completed=completed)
    for name, operation in operations.items():
        graph.add_node(name, guarded(name, partial(operation, deps=deps)
                                    if name not in {"prepare_approval", "finalize", "completed"}
                                    else operation, deps))
    graph.add_node("handle_failure", partial(handle_failure, deps=deps))

    def branch(source, router, destinations):
        graph.add_conditional_edges(
            source, lambda state: "handle_failure" if state.get("error") else router(state),
            {n: n for n in [*destinations, "handle_failure"]},
        )

    def edge(source, target):
        branch(source, lambda _: target, [target])

    if deps.unified is not None:
        graph.add_conditional_edges(START, lambda s: 'find_customers' if
                                    s.get('execution_context', {}).get('kind') == 'review' else 'receive',
                                    {'find_customers': 'find_customers', 'receive': 'receive'})
        edge('receive', 'classify')
        edge('classify', 'extract_flags')
        edge('calculate_unified', 'explain_risk')
    else:
        graph.add_edge(START, "receive")
        for first, second in [("classify", "mark_red"),
                          ("mark_red", "extract_flags"), ("score_orange", "compute_indicators"),
                          ("compute_indicators", "score_yellow"), ("score_yellow", "judge_stage"),
                          ("judge_stage", "explain_risk"), ('receive', 'classify')]:
            edge(first, second)
    for first, second in [("save_and_summarize", "completed"),
                          ("draft_messages", "guardrail_check"), ("prepare_approval", "pb_approval"),
                          ("pb_approval", "save_rejection"), ("save_rejection", "create_tasks")]:
        edge(first, second)
    def after_extraction(state):
        if deps.unified is not None:
            return 'calculate_unified' if state.get('flag_extraction_complete') else 'extract_flags'
        if any(route['text_flags'] for route in state['risk_routes']) and not state.get('flags'):
            return 'extract_flags'
        return 'score_orange'

    branch("extract_flags", after_extraction,
           ["extract_flags", 'calculate_unified' if deps.unified else 'score_orange'])
    branch("explain_risk", lambda s: "explain_risk" if s.get("explanation_errors") else "save_state",
           ["explain_risk", "save_state"])
    branch("save_state", lambda s: 'completed' if deps.unified else ("find_customers" if s["stage"] in ALERT_STAGES
           else "save_and_summarize"), ["completed", "find_customers", "save_and_summarize"])
    branch("find_customers", lambda s: "draft_messages" if pending_customers(s) else "save_and_summarize",
           ["draft_messages", "save_and_summarize"])
    branch("guardrail_check", lambda s: "draft_messages" if s["rewrite_customers"] else "prepare_approval",
           ["draft_messages", "prepare_approval"])
    branch("create_tasks", lambda s: "create_tasks" if any(
        d["status"] == "approved" and cid not in s["customer_tasks"] for cid, d in s["decisions"].items()
    ) else "finalize", ["create_tasks", "finalize"])
    branch("finalize", lambda s: "draft_messages" if s["rewrite_customers"] else (
        "prepare_approval" if pending_customers(s) else "completed"),
        ["draft_messages", "prepare_approval", "completed"])
    graph.add_edge("completed", END)
    graph.add_edge("handle_failure", END)
    return graph.compile(checkpointer=checkpointer)
