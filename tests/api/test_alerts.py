"""경보 목록·상세 API (가짜 Graph로 한 건을 실행한 뒤). (담당: R4)"""
import pytest

from tests.api.helpers import RCEPT, read_events


@pytest.fixture
def done(client, auth_headers):
    """가짜 Graph로 공시 1건을 끝까지 실행한 상태."""
    client.post("/runs", json={"rcept_no": RCEPT}, headers=auth_headers)
    events = read_events(client, auth_headers)
    assert events[-1][0] == "done"
    return events[-1][1]["assessment_id"]


def test_alert_list_shows_the_finished_assessment(client, auth_headers, done):
    rows = client.get("/alerts", headers=auth_headers).json()
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "DONE" and row["level"] == "Critical" and row["rcept_no"] == RCEPT
    assert client.get("/alerts?level=Low", headers=auth_headers).json() == []
    assert len(client.get("/alerts?level=High&level=Critical", headers=auth_headers).json()) == 1


def test_alert_detail_has_reasons_evidence_and_summary(client, auth_headers, done):
    body = client.get(f"/alerts/{done}", headers=auth_headers).json()
    assert body["reasons"] and body["evidence"] and body["summary"]["headline"]
    # 고객 연락(안내문·승인·영향 고객)은 삭제됐다
    assert not {"customers", "pending", "guardrail_flags"} & set(body)


def test_removed_contact_routes_are_gone(client, auth_headers, done):
    assert client.get(f"/alerts/{done}/drafts", headers=auth_headers).status_code == 404
    assert client.post(f"/alerts/{done}/decision", headers=auth_headers, json={"decisions": []}).status_code in (404, 405)
    assert client.get("/tasks", headers=auth_headers).status_code == 404
