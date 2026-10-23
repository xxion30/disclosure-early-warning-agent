"""Real file SQLite / separate connections: no user's DB, MCP or paid model."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

import api.main as main_module
from api.auth import CurrentPB, get_current_pb
from api.deps import get_db
from api.graph_adapter import FakeGraphAdapter, get_graph_adapter
from api.run_manager import RunConflict, RunManager, get_run_manager
from scripts.data import seed_db
from shared.config import Settings
from shared.models import AuditLog, Base, ContactTask, RiskAssessment

RCEPT = '20250321802001'  # receipt already present in the repository seed
CUSTOMERS = ('C001', 'C003', 'C005', 'C007')


def decisions(action):
    return [{'customer_id': cid, 'action': action,
             **({'reject_reason': '프로토콜 검증'} if action == 'reject' else {})}
            for cid in CUSTOMERS]


@pytest.fixture
def file_world(tmp_path, monkeypatch):
    flags = {'fail_commit': False}

    class FileSession(Session):
        def commit(self):
            if self.info.get('request') and flags['fail_commit']:
                flags['fail_commit'] = False
                raise OperationalError('commit fixture', {}, RuntimeError('forced commit failure'))
            return super().commit()

    engine = create_engine(f'sqlite:///{tmp_path / "approvals.db"}',
                           connect_args={'check_same_thread': False, 'timeout': 0.2})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, class_=FileSession, expire_on_commit=False)

    @contextmanager
    def sessions():
        with SessionLocal() as db:
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    with sessions() as db:
        seed_db.seed_people(db)
        companies = seed_db.seed_companies(db)
        seed_db.seed_disclosures(db, companies)

    adapter = FakeGraphAdapter(delay=0, session_factory=sessions)
    manager = RunManager(adapter)

    def request_db():
        with sessions() as db:
            db.info['request'] = True
            yield db

    app = main_module.app
    monkeypatch.setattr(main_module, 'init_db', lambda: None)
    monkeypatch.setattr(main_module, 'get_settings', lambda: Settings(_env_file=None,
        LANGSMITH_TRACING=False, QUEUE_ENABLED=False, COLLECT_DISPATCH_ENABLED=False,
        PHASE1_RUNTIME_FACTORY=''))
    app.dependency_overrides.update({get_db: request_db,
        get_current_pb: lambda: CurrentPB(pb_id='P01', name='fixture'),
        get_graph_adapter: lambda: adapter, get_run_manager: lambda: manager})
    try:
        with TestClient(app) as client:
            async def prepare():
                channel = manager.start(RCEPT)
                await asyncio.wait_for(channel.task, timeout=5)
                assert channel.last_event == 'waiting_approval'
                return (await adapter.get_state(RCEPT))['assessment_id']

            aid = client.portal.call(prepare)

            def wait():
                async def finish():
                    await asyncio.wait_for(manager.get(RCEPT).task, timeout=5)
                client.portal.call(finish)

            yield SimpleNamespace(client=client, adapter=adapter, manager=manager, aid=aid,
                                  sessions=sessions, flags=flags, wait=wait,
                                  url=f'/alerts/{aid}/decision')
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def count(world, model):
    with world.sessions() as db:
        return db.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize('action,task_count', [('reject', 0), ('approve', 4), ('rewrite', 0)])
def test_file_sqlite_commits_before_graph_writes(file_world, monkeypatch, action, task_count):
    w = file_world
    original = w.adapter._set_assessment_status
    observed = []

    def check_committed(aid, status):
        if status == 'RUNNING':
            # A separate connection must already see all accepted audit entries.
            observed.append(count(w, AuditLog))
            assert observed[-1] == 4
        return original(aid, status)

    monkeypatch.setattr(w.adapter, '_set_assessment_status', check_committed)
    body = decisions(action)
    if action == 'approve':
        body[0]['edited_draft'] = '{고객명}님, 공시 내용을 확인하고 PB와 상담해 주세요.'
    response = w.client.post(w.url, json={'decisions': body})
    assert response.status_code == 202, response.text
    w.wait()
    assert observed == [4]
    assert w.manager.get(RCEPT).last_event == 'waiting_approval'
    assert (w.client.portal.call(w.adapter.get_state, RCEPT))['status'] == 'WAITING_APPROVAL'
    assert count(w, ContactTask) == task_count
    assert count(w, AuditLog) == 4


def test_commit_failure_cancels_reservation_and_can_retry(file_world):
    w = file_world
    w.flags['fail_commit'] = True
    body = {'decisions': decisions('reject')}
    response = w.client.post(w.url, json=body)
    assert response.status_code == 503
    assert 'commit fixture' not in response.text
    assert not w.manager.get(RCEPT).running
    assert w.manager.get(RCEPT).last_event == 'waiting_approval'
    assert (w.client.portal.call(w.adapter.get_state, RCEPT))['status'] == 'WAITING_APPROVAL'
    assert count(w, AuditLog) == count(w, ContactTask) == 0
    assert w.client.post(w.url, json=body).status_code == 202
    w.wait()
    assert w.manager.get(RCEPT).last_event == 'waiting_approval'
    assert count(w, AuditLog) == 4


def test_initial_graph_write_failure_keeps_pending_and_retry_creates_tasks_once(file_world, monkeypatch):
    w = file_world
    original = w.adapter._set_assessment_status
    failed = []

    def fail_once(aid, status):
        if status == 'RUNNING' and not failed:
            failed.append(True)
            raise OperationalError('update fixture', {}, RuntimeError('forced write failure'))
        return original(aid, status)

    monkeypatch.setattr(w.adapter, '_set_assessment_status', fail_once)
    body = {'decisions': decisions('approve')}
    assert w.client.post(w.url, json=body).status_code == 202
    w.wait()
    assert w.manager.get(RCEPT).last_event == 'failed'
    state = w.client.portal.call(w.adapter.get_state, RCEPT)
    assert state['status'] == 'WAITING_APPROVAL' and not state['decided']
    assert count(w, ContactTask) == 0
    assert w.client.post(w.url, json=body).status_code == 202
    w.wait()
    assert w.manager.get(RCEPT).last_event == 'waiting_approval'
    assert count(w, ContactTask) == 4
    # Both requests were accepted before execution; keep both audit attempts.
    assert count(w, AuditLog) == 8


def test_failure_after_task_commit_is_manual_and_cannot_replay(file_world, monkeypatch):
    w = file_world
    original = w.adapter._step

    async def fail_finalize(receipt, node, **finished):
        if node == 'finalize':
            raise RuntimeError('forced failure after task commit')
        async for event in original(receipt, node, **finished):
            yield event

    monkeypatch.setattr(w.adapter, '_step', fail_finalize)
    body = {'decisions': decisions('approve')}
    assert w.client.post(w.url, json=body).status_code == 202
    w.wait()
    assert w.manager.get(RCEPT).last_event == 'failed'
    assert (w.client.portal.call(w.adapter.get_state, RCEPT))['status'] == 'MANUAL_CHECK'
    with w.sessions() as db:
        assert db.get(RiskAssessment, w.aid).status == 'MANUAL_CHECK'
    assert count(w, ContactTask) == 4
    assert w.client.post(w.url, json=body).status_code == 409
    assert count(w, ContactTask) == count(w, AuditLog) == 4


def test_resume_reservation_blocks_duplicate_start_and_abort_keeps_old_events(file_world):
    w = file_world

    async def check():
        old = list(w.manager.get(RCEPT).history)
        with w.manager.reserve_resume(RCEPT, {'decisions': []}):
            assert w.manager.get(RCEPT).running
            # Let the gated task run: it must not enter the adapter or clear events.
            await asyncio.sleep(0)
            assert (await w.adapter.get_state(RCEPT))['status'] == 'WAITING_APPROVAL'
            assert w.manager.get(RCEPT).history == old
            for operation in (lambda: w.manager.resume(RCEPT, {}),
                              lambda: w.manager.start(RCEPT),
                              lambda: w.manager.mark_queued(RCEPT)):
                with pytest.raises(RunConflict):
                    operation()
        await asyncio.sleep(0)
        assert not w.manager.get(RCEPT).running
        assert w.manager.get(RCEPT).history == old

    w.client.portal.call(check)


def test_double_submission_does_not_add_audits_or_tasks(file_world, monkeypatch):
    w = file_world
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = w.adapter._step

    async def pause(receipt, node, **finished):
        if node == 'save_rejection':
            entered.set()
            await proceed.wait()
        async for event in original(receipt, node, **finished):
            yield event

    monkeypatch.setattr(w.adapter, '_step', pause)
    body = {'decisions': decisions('approve')}
    assert w.client.post(w.url, json=body).status_code == 202

    async def wait_entered():
        await asyncio.wait_for(entered.wait(), timeout=5)

    w.client.portal.call(wait_entered)
    try:
        assert w.client.post(w.url, json=body).status_code == 409
        assert count(w, AuditLog) == 4
    finally:
        w.client.portal.call(proceed.set)
    w.wait()
    assert count(w, ContactTask) == 4
