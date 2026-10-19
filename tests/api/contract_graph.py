"""API(C)가 기대하는 Graph 모양을 보여주는 작은 LangGraph — 테스트 전용.

진짜 agent/graph.py(A)를 대신해 RealGraphAdapter를 검증한다. LLM·MCP 없이 규칙만으로 움직인다.
A에게 보여줄 참고 코드이기도 하다. API가 기대하는 것:

1. build_graph(checkpointer) → 컴파일된 graph. 시작 입력은 {"disclosure": {"rcept_no": ...}}
2. High·Critical 경로에서 interrupt 전에 save_assessment → State.assessment_id
3. pb_approval은 PB 1명의 결정 묶음을 받는다:
     pb_approval [interrupt] → create_tasks(이번 묶음의 승인 고객만)
       → 다시 쓸 고객이 있으면 draft_messages → guardrail_check → pb_approval
       → 결정 안 된 P1·P2가 남았으면 pb_approval [interrupt], 없으면 END
4. 결정은 State.approval.decisions = {customer_id: 결정}에 쌓는다
5. interrupt()가 있는 노드는 재개할 때 처음부터 다시 실행된다.
   그래서 interrupt 앞의 DB 쓰기는 몇 번 실행돼도 결과가 같아야 한다(멱등). 새 행 INSERT는 별도 노드에서 한다.
"""
from __future__ import annotations

import json
from contextlib import AbstractContextManager
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy.orm import Session

from api.graph_adapter import FAKE_DRAFT, now_iso
from shared.config import ROOT_DIR
from shared.models import ContactTask, Customer, RiskAssessment

# 테스트가 메모리 DB 세션을 꽂아 넣는다
SESSION_FACTORY: Callable[[], AbstractContextManager[Session]] | None = None
FAIL_RCEPT_SUFFIX = "999"  # 이 번호로 끝나면 receive에서 일부러 실패 (Recovery 확인용)


class State(TypedDict, total=False):
    disclosure: dict
    risk_level: str
    total_score: float
    ai_explanation: str
    evidence: list[dict]
    assessment_id: int
    affected_customers: list[dict]
    drafts: dict[str, str]
    guardrail_flags: list[str]
    approval: dict  # {"decisions": {customer_id: 결정}}
    last_batch: dict  # 방금 받은 결정 묶음 {pb_id, decided_at, decisions}
    rewrite: list[str]
    task_ids: list[int]
    error: str | None


def _session():
    assert SESSION_FACTORY is not None, "테스트에서 contract_graph.SESSION_FACTORY를 설정해야 합니다"
    return SESSION_FACTORY()


def _pending(state: State) -> list[str]:
    decided = state.get("approval", {}).get("decisions", {})
    return [c["customer_id"] for c in state.get("affected_customers", [])
            if c["priority"] in ("P1", "P2") and c["customer_id"] not in decided]


# ---- 노드 ----
def receive(state: State) -> dict:
    rcept_no = state["disclosure"]["rcept_no"]
    if rcept_no.endswith(FAIL_RCEPT_SUFFIX):
        raise RuntimeError("get_disclosure: MCP TIMEOUT (테스트용 실패)")
    return {"disclosure": {"rcept_no": rcept_no, "stock_code": "001570", "title": "[CONTRACT] 감사의견 거절"},
            "approval": {"decisions": {}}, "task_ids": [], "rewrite": [], "guardrail_flags": [], "error": None}


def classify(state: State) -> dict:
    return {}


def extract_facts(state: State) -> dict:
    return {"evidence": [{"source": "[CONTRACT]", "quote": "[CONTRACT] 인용", "rcept_no": state["disclosure"]["rcept_no"]}]}


def score_risk(state: State) -> dict:
    return {"risk_level": "Critical", "total_score": 0}


def explain_risk(state: State) -> dict:
    return {"ai_explanation": "[CONTRACT] A그룹 확정 악재"}


def find_customers(state: State) -> dict:
    """High·Critical 경로 첫 노드에서 save_assessment (interrupt 노드가 아니라 한 번만 실행된다)."""
    rows = json.loads((ROOT_DIR / "data" / "customers.json").read_text(encoding="utf-8"))
    customers = [{k: r[k] for k in ("customer_id", "impact_score", "impact_level", "priority")} | {"reasons": {}}
                 for r in rows["expected_impact_001570_critical"]]
    with _session() as s:
        row = RiskAssessment(corp_code="TODO-001570", rcept_no=state["disclosure"]["rcept_no"], total_score=0,
                             level=state["risk_level"], reasons=[{"item": "[CONTRACT]"}],
                             ai_explanation=state["ai_explanation"], status="RUNNING", created_at=now_iso())
        s.add(row)
        s.commit()
        assessment_id = row.assessment_id
    return {"affected_customers": customers, "assessment_id": assessment_id}


def draft_messages(state: State) -> dict:
    drafts = dict(state.get("drafts", {}))
    targets = state.get("rewrite") or [c["customer_id"] for c in state["affected_customers"] if c["priority"] in ("P1", "P2")]
    for cid in targets:
        drafts[cid] = FAKE_DRAFT.replace("[FAKE 초안]", "[CONTRACT 다시 작성]" if state.get("rewrite") else "[CONTRACT 초안]")
    return {"drafts": drafts, "rewrite": []}


def guardrail_check(state: State) -> dict:
    return {"guardrail_flags": []}


def _set_status(assessment_id: int, status: str) -> None:
    with _session() as s:
        s.get(RiskAssessment, assessment_id).status = status
        s.commit()


def pb_approval(state: State) -> dict:
    _set_status(state["assessment_id"], "WAITING_APPROVAL")  # 멱등: 재개 때 다시 실행돼도 같은 결과
    batch = interrupt({"pending": _pending(state)})

    decisions = dict(state["approval"]["decisions"])
    rewrite = []
    for d in batch["decisions"]:
        if d["action"] == "rewrite":
            rewrite.append(d["customer_id"])
        else:
            decisions[d["customer_id"]] = {**d, "pb_id": batch["pb_id"], "decided_at": batch["decided_at"]}
    _set_status(state["assessment_id"], "RUNNING")
    return {"approval": {"decisions": decisions}, "last_batch": batch, "rewrite": rewrite}


def create_tasks(state: State) -> dict:
    """이번 묶음의 승인 고객만 업무로 만든다 (create_contact_task)."""
    batch = state["last_batch"]
    impacts = {c["customer_id"]: c for c in state["affected_customers"]}
    ids = list(state.get("task_ids", []))
    with _session() as s:
        for d in batch["decisions"]:
            if d["action"] != "approve":
                continue
            cid, now = d["customer_id"], now_iso()
            assert s.get(Customer, cid).pb_id == batch["pb_id"]
            t = ContactTask(assessment_id=state["assessment_id"], customer_id=cid, pb_id=batch["pb_id"],
                            priority=impacts[cid]["priority"], impact_score=impacts[cid]["impact_score"],
                            draft_message=d.get("edited_draft") or state["drafts"][cid], status="승인",
                            created_at=now, updated_at=now)
            s.add(t)
            s.flush()
            ids.append(t.task_id)
        s.commit()
    if not state.get("rewrite") and not _pending(state):
        _set_status(state["assessment_id"], "DONE")
    return {"task_ids": ids}


def after_create_tasks(state: State) -> str:
    if state.get("rewrite"):
        return "draft_messages"
    return "pb_approval" if _pending(state) else END


def build_graph(checkpointer: Any):
    g = StateGraph(State)
    for fn in (receive, classify, extract_facts, score_risk, explain_risk, find_customers,
               draft_messages, guardrail_check, pb_approval, create_tasks):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "receive")
    g.add_edge("receive", "classify")
    g.add_edge("classify", "extract_facts")
    g.add_edge("extract_facts", "score_risk")
    g.add_edge("score_risk", "explain_risk")
    g.add_edge("explain_risk", "find_customers")
    g.add_edge("find_customers", "draft_messages")
    g.add_edge("draft_messages", "guardrail_check")
    g.add_edge("guardrail_check", "pb_approval")
    g.add_edge("pb_approval", "create_tasks")
    g.add_conditional_edges("create_tasks", after_create_tasks, ["draft_messages", "pb_approval", END])
    return g.compile(checkpointer=checkpointer)
