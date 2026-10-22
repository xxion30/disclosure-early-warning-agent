"""큐 모드 (가산점) — 메모리 브로커로 API·소비자 로직을, 진짜 RabbitMQ가 있으면 지연 재시도 경로까지 확인한다."""
import asyncio
import os

import pytest

from api.graph_adapter import FAILED, FakeGraphAdapter, make_event
from api.routes.runs import get_queue
from api.main import app
from api.run_manager import RunManager
from api.worker.consumer import DisclosureConsumer
from api.worker.queue import DLQ, MAIN_QUEUE, RETRY_QUEUE
from tests.api.helpers import RCEPT, read_events

R2, R3 = "20250401000002", "20250401000003"


class MemoryBackend:
    """RabbitMQ 대신 쓰는 메모리 큐. 지연(expiration)은 기록만 하고 move_retries()로 돌려보낸다."""

    def __init__(self):
        self.queues = {MAIN_QUEUE: [], RETRY_QUEUE: [], DLQ: []}
        self.delays = []

    async def publish(self, queue, body, delay_seconds=None):
        self.queues[queue].append(dict(body))
        if delay_seconds:
            self.delays.append(delay_seconds)

    def move_retries(self):  # RabbitMQ에서는 expiration이 끝나면 dead-letter로 disclosures에 돌아간다
        self.queues[MAIN_QUEUE].extend(self.queues[RETRY_QUEUE])
        self.queues[RETRY_QUEUE].clear()

    async def drain(self, consumer):
        while self.queues[MAIN_QUEUE]:
            await consumer.handle(self.queues[MAIN_QUEUE].pop(0))

    async def stats(self):
        return {k: len(v) for k, v in self.queues.items()}

    async def close(self):
        pass


class FlakyAdapter(FakeGraphAdapter):
    """처음 fail_times번은 체크포인트가 생기기 전에 실패(MCP 서버 꺼짐 흉내). with_checkpoint면 기록이 남는 실패."""

    def __init__(self, fail_times=0, with_checkpoint=False, **kw):
        super().__init__(**kw)
        self.fail_times = fail_times
        self.with_checkpoint = with_checkpoint
        self.calls = 0

    async def run(self, rcept_no):
        self.calls += 1
        if self.calls <= self.fail_times:
            if self.with_checkpoint:
                self._states[rcept_no] = {"status": "MANUAL_CHECK"}
            yield make_event(FAILED, rcept_no, message="agent.Dependencies 조립 실패 (ConnectError)")
            return
        async for ev in super().run(rcept_no):
            yield ev


@pytest.fixture
def backend():
    return MemoryBackend()


@pytest.fixture
def queued_client(client, backend):
    app.dependency_overrides[get_queue] = lambda: backend
    return client


# ---- API ----
def test_post_runs_in_queue_mode_enqueues_then_consumer_runs(queued_client, auth_headers, backend, manager, adapter):
    res = queued_client.post("/runs", json={"rcept_no": RCEPT}, headers=auth_headers)
    assert res.status_code == 202 and res.json()["status"] == "QUEUED"
    assert backend.queues[MAIN_QUEUE] == [{"rcept_no": RCEPT, "attempt": 1}]
    assert queued_client.get(f"/runs/{RCEPT}", headers=auth_headers).json()["last_event"] == "queued"
    # 대기 중에 다시 넣으면 그대로 받아 준다(아직 실행 전), 실행이 끝난 뒤에는 409
    consumer = DisclosureConsumer(manager, adapter, backend)
    queued_client.portal.call(backend.drain, consumer)
    assert read_events(queued_client, auth_headers)[-1][0] == "waiting_approval"
    assert queued_client.post("/runs", json={"rcept_no": RCEPT}, headers=auth_headers).status_code == 409


def test_sse_attached_while_queued_sees_whole_run(queued_client, auth_headers, backend, manager, adapter):
    queued_client.post("/runs", json={"rcept_no": RCEPT}, headers=auth_headers)
    consumer = DisclosureConsumer(manager, adapter, backend)

    async def watch_then_consume():
        names = []

        async def watch():
            async for ev in manager.subscribe(RCEPT):
                if ev is not None:
                    names.append(ev["event"])

        task = asyncio.create_task(watch())
        await asyncio.sleep(0)  # 구독 먼저
        await backend.drain(consumer)
        await asyncio.wait_for(task, 5)
        return names

    names = queued_client.portal.call(watch_then_consume)
    assert names[0] == "queued" and names[-1] == "waiting_approval" and "node_started" in names


def test_batch(queued_client, auth_headers, backend):
    res = queued_client.post("/runs/batch", json={"rcept_nos": [RCEPT, R2, R2, R3]}, headers=auth_headers)
    assert res.status_code == 202 and res.json() == {"queued": [RCEPT, R2, R3], "skipped": []}
    assert [m["rcept_no"] for m in backend.queues[MAIN_QUEUE]] == [RCEPT, R2, R3]
    assert queued_client.post("/runs/batch", json={"rcept_nos": ["123"]}, headers=auth_headers).status_code == 422
    stats = queued_client.get("/runs/queue", headers=auth_headers).json()
    assert stats["enabled"] and stats["queues"][MAIN_QUEUE] == 3


def test_batch_needs_queue_mode(client, auth_headers):
    assert client.post("/runs/batch", json={"rcept_nos": [RCEPT]}, headers=auth_headers).status_code == 409
    assert client.get("/runs/queue", headers=auth_headers).json() == {"enabled": False}


# ---- 소비자 ----
def run(coro):
    return asyncio.run(coro)


def test_transient_failure_is_retried_until_success(backend):
    async def scenario():
        adapter = FlakyAdapter(fail_times=2, delay=0, session_factory=_nodb)
        consumer = DisclosureConsumer(RunManager(adapter), adapter, backend)
        await backend.publish(MAIN_QUEUE, {"rcept_no": RCEPT, "attempt": 1})
        for _ in range(3):
            await backend.drain(consumer)
            backend.move_retries()
        return consumer.processed

    processed = run(scenario())
    assert [p[2] for p in processed] == ["retry in 3s", "retry in 10s", "waiting_approval"]
    assert backend.delays == [3, 10] and not backend.queues[DLQ]


def test_retries_exhausted_go_to_dlq(backend):
    async def scenario():
        adapter = FlakyAdapter(fail_times=99, delay=0, session_factory=_nodb)
        consumer = DisclosureConsumer(RunManager(adapter), adapter, backend)
        await backend.publish(MAIN_QUEUE, {"rcept_no": RCEPT, "attempt": 1})
        for _ in range(4):
            await backend.drain(consumer)
            backend.move_retries()
        return adapter.calls

    assert run(scenario()) == 3
    dead = backend.queues[DLQ]
    assert len(dead) == 1 and dead[0]["attempt"] == 3 and "조립 실패" in dead[0]["error"]


def test_failure_with_checkpoint_goes_straight_to_dlq(backend):
    async def scenario():
        adapter = FlakyAdapter(fail_times=1, with_checkpoint=True, delay=0, session_factory=_nodb)
        consumer = DisclosureConsumer(RunManager(adapter), adapter, backend)
        await consumer.handle({"rcept_no": RCEPT, "attempt": 1})

    run(scenario())
    assert not backend.queues[RETRY_QUEUE]
    assert backend.queues[DLQ][0]["reason"].startswith("실행 기록이 남은 실패")


def test_already_processed_is_skipped(backend):
    async def scenario():
        adapter = FlakyAdapter(delay=0, session_factory=_nodb)
        manager = RunManager(adapter)
        consumer = DisclosureConsumer(manager, adapter, backend)
        await consumer.handle({"rcept_no": RCEPT})
        await consumer.handle({"rcept_no": RCEPT})  # 같은 메시지가 두 번 와도 다시 실행하지 않는다
        return consumer.processed

    assert [p[2] for p in run(scenario())] == ["waiting_approval", "skip"]


class _NoDb:
    """소비자 테스트는 DB를 보지 않는다. FakeGraphAdapter의 DB 쓰기를 무시한다."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a):
        return None

    def scalar(self, *a):
        return None

    def add(self, row):
        row.assessment_id = 1

    def commit(self):
        pass


def _nodb():
    return _NoDb()


# ---- 진짜 RabbitMQ (docker compose up -d rabbitmq 일 때만) ----
def _broker_up() -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", 5672)) == 0


@pytest.mark.skipif(not _broker_up() or os.getenv("SKIP_RABBITMQ"), reason="RabbitMQ(5672)가 없음")
def test_real_rabbitmq_delayed_retry_returns_to_main_queue():
    """retry 큐의 expiration이 끝나면 dead-letter로 disclosures에 돌아와 다시 처리되는지 진짜 브로커로 확인."""
    from api.worker.queue import AioPikaBackend
    from shared.config import get_settings

    async def scenario():
        # 실행 중인 API 소비자와 섞이지 않게 테스트 전용 큐를 쓴다
        backend = await AioPikaBackend.connect(get_settings().RABBITMQ_URL, prefix="pytest.")
        try:
            await backend.purge()
            adapter = FlakyAdapter(fail_times=1, delay=0, session_factory=_nodb)
            consumer = DisclosureConsumer(RunManager(adapter), adapter, backend, retry_delays=(1,))
            task = asyncio.create_task(consumer.run_forever())
            await backend.publish(MAIN_QUEUE, {"rcept_no": RCEPT, "attempt": 1})
            for _ in range(60):
                if any(p[2] == "waiting_approval" for p in consumer.processed):
                    break
                await asyncio.sleep(0.2)
            task.cancel()
            return consumer.processed, await backend.stats()
        finally:
            await backend.close()

    processed, stats = asyncio.run(scenario())
    assert [p[2] for p in processed] == ["retry in 1s", "waiting_approval"]
    assert [p[1] for p in processed] == [1, 2]
    assert stats == {MAIN_QUEUE: 0, RETRY_QUEUE: 0, DLQ: 0}
