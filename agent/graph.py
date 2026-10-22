"""Deterministic orchestration; models cannot choose mutation tools or scores.

흐름: receive → classify → extract_facts(근거 부족 시 재시도) → score_risk → explain_risk → save_and_summarize → completed.
고객 연락(안내문·PB 승인·연락 업무)은 DARTCH에서 삭제했다. 승인 대기(interrupt)는 없다.
새 그래프(🔴/🟠/🟡 단계 판정)는 R2가 이 뼈대 위에서 다시 쓴다 (docs/dartch_phase1_mvp.md STEP 05).
"""
from functools import partial

from langgraph.graph import END, START, StateGraph

from agent.execution import guarded
from agent.nodes.classify import classify
from agent.nodes.explain_risk import explain_risk
from agent.nodes.extract_facts import extract_facts
from agent.nodes.handle_failure import handle_failure
from agent.nodes.receive import receive
from agent.nodes.save_and_summarize import save_and_summarize
from agent.nodes.score_risk import score_risk
from agent.state import AgentState


def build_graph(deps, checkpointer):
    graph = StateGraph(AgentState)
    operations = {
        "receive": receive, "classify": classify, "extract_facts": extract_facts,
        "score_risk": score_risk, "explain_risk": explain_risk,
        "save_and_summarize": save_and_summarize,
    }

    async def completed(state):
        await deps.harness.event("done", {"trace_id": state["trace_id"], "status": state["status"]})
        return {}

    for name, operation in operations.items():
        graph.add_node(name, guarded(name, partial(operation, deps=deps), deps))
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
    branch("extract_facts", lambda s: "completed" if s.get("no_signal") else (
        "score_risk" if s.get("extracted_facts") else "extract_facts"), ["score_risk", "extract_facts", "completed"])
    edge("score_risk", "explain_risk")
    edge("explain_risk", "save_and_summarize")
    edge("save_and_summarize", "completed")
    graph.add_edge("completed", END)
    graph.add_edge("handle_failure", END)
    return graph.compile(checkpointer=checkpointer)
