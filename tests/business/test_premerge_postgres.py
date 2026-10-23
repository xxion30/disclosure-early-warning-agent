"""Real PostgreSQL concurrency checks in an isolated schema."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime,timezone,timedelta
from sqlalchemy.orm import Session
from shared.models import Base,Company,Disclosure,AnalysisJob
from shared.analysis import mark_ready,enqueue,claim,recover_expired,heartbeat,locked_job,LostLease
from tests.business.test_postgres_review import postgres
import pytest


def test_postgres_only_one_worker_claims_and_expired_token_is_fenced(postgres):
    Base.metadata.create_all(postgres)
    with Session(postgres) as db:
        db.add(Company(corp_code='fixture',stock_code='001570',name='contract'));db.flush()
        db.add(Disclosure(rcept_no='20250321802001',corp_code='fixture',title='contract',filed_at='2025-03-21'));db.flush()
        mark_ready(db,'20250321802001');enqueue(db,'20250321802001','2025-03-21','rules-fixture');db.commit()
    barrier=Barrier(2)
    clock=datetime.now(timezone.utc)
    def take():
        with Session(postgres,expire_on_commit=False) as db:
            barrier.wait(timeout=10)
            row=claim(db,now=clock);db.commit()
            return (row.job_id,row.lease_token) if row else None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[f.result(timeout=20) for f in [pool.submit(take),pool.submit(take)]]
    assert sum(v is not None for v in results)==1
    job_id,old=next(v for v in results if v)
    with Session(postgres) as db:
        assert recover_expired(db,now=clock+timedelta(seconds=121))==1;db.commit()
        fresh=claim(db,now=clock+timedelta(seconds=121));assert fresh.attempts==2
        assert not heartbeat(db,job_id,old,now=clock+timedelta(seconds=121))
        with pytest.raises(LostLease):locked_job(db,job_id,old,now=clock+timedelta(seconds=121))
        db.commit()


def test_postgres_checkpointer_is_injected_and_round_trips(postgres):
    import asyncio
    from agent.adapters.checkpoints import open_checkpointer
    from langgraph.graph import StateGraph,START,END
    from typing import TypedDict
    class State(TypedDict):value:str
    async def run():
        builder=StateGraph(State);builder.add_node('keep',lambda state:state)
        builder.add_edge(START,'keep');builder.add_edge('keep',END)
        url=postgres.url.render_as_string(hide_password=False)
        async with open_checkpointer(url) as saver:
            graph=builder.compile(checkpointer=saver)
            await graph.ainvoke({'value':'public-contract'},config={'configurable':{'thread_id':'public:fixture'}})
        async with open_checkpointer(url) as saver:
            state=await builder.compile(checkpointer=saver).aget_state({'configurable':{'thread_id':'public:fixture'}})
            assert state.values['value']=='public-contract'
    asyncio.run(run())


def test_different_jobs_of_one_issuer_cannot_run_concurrently(postgres):
    Base.metadata.create_all(postgres)
    with Session(postgres) as db:
        db.add(Company(corp_code='issuer', stock_code='001570', name='protocol')); db.flush()
        for receipt in ('20250321802001', '20250321802002'):
            db.add(Disclosure(rcept_no=receipt, corp_code='issuer', title='protocol', filed_at='2025-03-21'))
        db.flush()
        for receipt in ('20250321802001', '20250321802002'):
            mark_ready(db, receipt); enqueue(db, receipt, '2025-03-21', 'rules-protocol')
        db.commit()
    barrier = Barrier(2)
    def take():
        with Session(postgres) as db:
            barrier.wait(timeout=10)
            row = claim(db)
            result = row.job_id if row else None
            db.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(take), pool.submit(take)]
        assert sum(f.result(timeout=20) is not None for f in futures) == 1
    with Session(postgres) as db:
        assert claim(db) is None
