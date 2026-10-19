"""CLAUDE.md 9장 시나리오 A 8~13단계를 API로 끝까지 따라간다 (가짜 Graph)."""
import pytest
from sqlalchemy import select

from shared.models import AuditLog, ContactTask
from tests.api.helpers import RCEPT, read_events

P01_CUSTOMERS = ["C001", "C003", "C005", "C007"]
P02_CUSTOMERS = ["C002", "C004", "C006"]
EDITED = "{고객명}님, 감사의견 관련 공시 내용을 정리해 내일 오전에 직접 전화드리겠습니다."


@pytest.fixture
def waiting(client, auth_headers):
    """8단계: Graph가 pb_approval에서 멈춘 상태."""
    client.post("/runs", json={"rcept_no": RCEPT}, headers=auth_headers)
    events = read_events(client, auth_headers)
    assert events[-1][0] == "waiting_approval"
    return events[-1][1]["assessment_id"]


def decide(client, headers, assessment_id, decisions):
    return client.post(f"/alerts/{assessment_id}/decision", json={"decisions": decisions}, headers=headers)


def tasks_in_db(session_factory):
    with session_factory() as s:
        return {t.customer_id: t for t in s.scalars(select(ContactTask))}


def test_alert_list_shows_waiting(client, auth_headers, waiting):
    rows = client.get("/alerts", headers=auth_headers).json()
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "WAITING_APPROVAL" and row["level"] == "Critical"
    assert row["affected_count"] == 7 and row["p1_count"] == 5
    assert row["my_pending_count"] == 4  # P01 담당 C001, C003, C005, C007

    assert client.get("/alerts?level=Low", headers=auth_headers).json() == []
    assert len(client.get("/alerts?level=High&level=Critical", headers=auth_headers).json()) == 1


def test_alert_detail_masks_other_pb_names(client, auth_headers, waiting):
    body = client.get(f"/alerts/{waiting}", headers=auth_headers).json()
    customers = {c["customer_id"]: c for c in body["customers"]}
    assert [c["priority"] for c in body["customers"]] == ["P1"] * 5 + ["P2"] * 2  # 우선순위순
    assert customers["C001"]["name"] == "한지우" and customers["C001"]["mine"]
    assert customers["C002"]["name"] == "고객 C002" and not customers["C002"]["mine"]
    assert body["reasons"] and body["evidence"]


def test_drafts_only_mine_with_placeholder(client, auth_headers, waiting):
    drafts = client.get(f"/alerts/{waiting}/drafts", headers=auth_headers).json()["drafts"]
    assert sorted(d["customer_id"] for d in drafts) == P01_CUSTOMERS
    d = drafts[0]
    assert "{고객명}" in d["draft"]
    assert "{고객명}" not in d["preview"] and d["name"] in d["preview"]


def test_full_scenario_a(client, auth_headers, p02_headers, waiting, session_factory):
    # 12단계 일부: P01은 P02 고객을 결정할 수 없다
    res = decide(client, auth_headers, waiting, [{"customer_id": "C002", "action": "approve"}])
    assert res.status_code == 403

    # 9단계: P01 — C001은 한 문장 고쳐서 승인, 나머지 3명은 그대로 승인
    res = decide(client, auth_headers, waiting, [
        {"customer_id": "C001", "action": "approve", "edited_draft": EDITED},
        *({"customer_id": c, "action": "approve"} for c in P01_CUSTOMERS[1:]),
    ])
    assert res.status_code == 202, res.text
    events = read_events(client, auth_headers)
    assert events[-1][0] == "waiting_approval"
    assert sorted(events[-1][1]["pending"]) == P02_CUSTOMERS

    # 10단계: 업무 4건이 "승인"으로 생기고, C001은 수정한 문장
    tasks = tasks_in_db(session_factory)
    assert sorted(tasks) == P01_CUSTOMERS
    assert all(t.status == "승인" for t in tasks.values())
    assert tasks["C001"].draft_message == EDITED
    assert client.get("/alerts", headers=auth_headers).json()[0]["status"] == "WAITING_APPROVAL"

    # 같은 고객을 다시 결정할 수 없다
    assert decide(client, auth_headers, waiting, [{"customer_id": "C001", "action": "approve"}]).status_code == 409

    # 11단계: P02 — 반려는 사유 필수
    assert decide(client, p02_headers, waiting, [{"customer_id": "C002", "action": "reject"}]).status_code == 422
    res = decide(client, p02_headers, waiting, [
        {"customer_id": "C002", "action": "reject", "reject_reason": "고객이 이미 공시 내용을 알고 있음"},
        {"customer_id": "C004", "action": "approve"},
        {"customer_id": "C006", "action": "approve"},
    ])
    assert res.status_code == 202, res.text
    assert read_events(client, p02_headers)[-1][0] == "done"

    # 12단계: 업무 2건 추가(반려된 C002는 업무 없음), 반려 사유 저장
    tasks = tasks_in_db(session_factory)
    assert sorted(tasks) == sorted(P01_CUSTOMERS + ["C004", "C006"])
    assert {t.pb_id for c, t in tasks.items() if c in ("C004", "C006")} == {"P02"}
    detail = client.get(f"/alerts/{waiting}", headers=p02_headers).json()
    assert {c["customer_id"]: c["decision"] for c in detail["customers"]}["C002"] == "반려"
    assert detail["status"] == "DONE"

    # 13단계: audit_logs에 결정 7건
    with session_factory() as s:
        logs = s.scalars(select(AuditLog).where(AuditLog.action.startswith("decision."))).all()
    assert len(logs) == 7
    reject = next(l for l in logs if l.action == "decision.reject")
    assert reject.actor == "P02" and reject.payload["reject_reason"] == "고객이 이미 공시 내용을 알고 있음"
    assert next(l for l in logs if l.target.endswith("C001")).action == "decision.approve_edited"


def test_real_name_in_edit_is_masked(client, auth_headers, waiting, session_factory):
    decide(client, auth_headers, waiting, [{"customer_id": "C001", "action": "approve", "edited_draft": "한지우님, 연락드리겠습니다."}])
    read_events(client, auth_headers)
    assert tasks_in_db(session_factory)["C001"].draft_message == "{고객명}님, 연락드리겠습니다."


def test_rewrite_keeps_customer_pending(client, auth_headers, waiting):
    res = decide(client, auth_headers, waiting, [{"customer_id": "C003", "action": "rewrite"}])
    assert res.status_code == 202
    events = read_events(client, auth_headers)
    assert "C003" in events[-1][1]["pending"]
    drafts = {d["customer_id"]: d for d in client.get(f"/alerts/{waiting}/drafts", headers=auth_headers).json()["drafts"]}
    assert "다시 작성" in drafts["C003"]["draft"]


def test_tasks_list_and_patch(client, auth_headers, p02_headers, waiting):
    decide(client, auth_headers, waiting, [{"customer_id": c, "action": "approve"} for c in P01_CUSTOMERS])
    read_events(client, auth_headers)

    mine = client.get("/tasks", headers=auth_headers).json()
    assert sorted(t["customer_id"] for t in mine) == P01_CUSTOMERS
    assert client.get("/tasks", headers=p02_headers).json() == []
    task = mine[0]
    assert "{고객명}" not in task["message_preview"]

    assert client.patch(f"/tasks/{task['task_id']}", json={"status": "연락완료"}, headers=p02_headers).status_code == 403
    assert client.patch(f"/tasks/{task['task_id']}", json={"status": "반려"}, headers=auth_headers).status_code == 422

    res = client.patch(f"/tasks/{task['task_id']}", json={"status": "보류", "note": "부재중"}, headers=auth_headers)
    assert res.status_code == 200 and res.json()["status"] == "보류" and res.json()["result_note"] == "부재중"
    res = client.patch(f"/tasks/{task['task_id']}", json={"status": "연락완료", "note": "통화 완료"}, headers=auth_headers)
    assert res.json()["status"] == "연락완료"
    # 연락완료 뒤에는 더 바꿀 수 없다
    assert client.patch(f"/tasks/{task['task_id']}", json={"status": "보류"}, headers=auth_headers).status_code == 409
    assert client.get("/tasks?status=연락완료", headers=auth_headers).json()[0]["task_id"] == task["task_id"]
    assert client.patch("/tasks/9999", json={"status": "보류"}, headers=auth_headers).status_code == 404
