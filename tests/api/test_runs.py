"""POST /runs, GET /runs/{rcept_no}, SSE /runs/{rcept_no}/events 테스트 (가짜 Graph)."""
import asyncio

from api.graph_adapter import FAILED, FakeGraphAdapter, make_event
from api.run_manager import RunManager
from tests.api.helpers import RCEPT, login, parse_sse, read_events


def test_start_requires_login(client):
    assert client.post("/runs", json={"rcept_no": RCEPT}).status_code == 401


def test_start_rejects_bad_rcept_no(client, auth_headers):
    assert client.post("/runs", json={"rcept_no": "123"}, headers=auth_headers).status_code == 422


def test_run_streams_until_waiting_approval(client, auth_headers):
    res = client.post("/runs", json={"rcept_no": RCEPT}, headers=auth_headers)
    assert res.status_code == 202
    assert res.json()["events_url"] == f"/runs/{RCEPT}/events"

    events = read_events(client, auth_headers)
    names = [e for e, _ in events]
    assert names[0] == "node_started" and events[0][1]["node"] == "receive"
    assert names[-1] == "waiting_approval"
    finished = [d["node"] for e, d in events if e == "node_finished"]
    assert finished[:3] == ["receive", "classify", "extract_facts"]
    assert "guardrail_check" in finished

    last = events[-1][1]
    assert last["affected"] == 7  # CLAUDE.md 9장 시나리오 A 6단계
    assert last["assessment_id"] == 1

    status = client.get(f"/runs/{RCEPT}", headers=auth_headers).json()
    assert status["running"] is False and status["last_event"] == "waiting_approval"


def test_sse_accepts_token_query(client):
    token = login(client).json()["access_token"]
    client.post("/runs", json={"rcept_no": RCEPT}, headers={"Authorization": f"Bearer {token}"})
    with client.stream("GET", f"/runs/{RCEPT}/events?token={token}") as res:
        assert res.status_code == 200
        assert parse_sse(res.read().decode())[-1][0] == "waiting_approval"


def test_sse_requires_login(client, auth_headers):
    client.post("/runs", json={"rcept_no": RCEPT}, headers=auth_headers)
    assert client.get(f"/runs/{RCEPT}/events").status_code == 401


def test_unknown_run_is_404(client, auth_headers):
    assert client.get("/runs/20990101000000/events", headers=auth_headers).status_code == 404
    assert client.get("/runs/20990101000000", headers=auth_headers).status_code == 404


def test_duplicate_start_is_409(client, auth_headers):
    assert client.post("/runs", json={"rcept_no": RCEPT}, headers=auth_headers).status_code == 202
    assert client.post("/runs", json={"rcept_no": RCEPT}, headers=auth_headers).status_code == 409


class _BrokenAdapter(FakeGraphAdapter):
    async def run(self, rcept_no):
        yield make_event("node_started", rcept_no, node="receive")
        raise RuntimeError("MCP 서버에 연결할 수 없음")


def test_failure_becomes_failed_event_and_can_restart():
    async def scenario():
        manager = RunManager(_BrokenAdapter(delay=0))
        ch = manager.start(RCEPT)
        await ch.task
        assert ch.last_event == FAILED
        assert "MCP 서버" in ch.history[-1]["data"]["message"]
        manager.start(RCEPT)  # 실패한 공시는 다시 시작할 수 있다

    asyncio.run(scenario())
