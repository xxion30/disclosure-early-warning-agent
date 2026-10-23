"""DB worker. The injected R2 public executor must not perform private/customer writes."""
import asyncio
from contextlib import suppress
from dataclasses import dataclass
from shared.analysis import claim, heartbeat, fail_job, LostLease
from shared.db import get_session
from shared.config import get_settings
from mcp_servers.business.publication import complete_analysis


class TransientAnalysisError(Exception):
    pass


class ManualAnalysisError(Exception):
    pass


@dataclass(frozen=True)
class AnalysisContext:
    job_id: str
    corp_code: str
    rcept_no: str
    as_of: str
    input_manifest: dict
    input_hash: str
    rules_digest: str
    thread_id: str
    attempt: int


class AnalysisWorker:
    def __init__(self, executor, *, session_factory=get_session):
        if not callable(executor):raise ValueError('R2 공개 분석 executor가 필요합니다.')
        self.executor=executor
        self.sessions=session_factory

    async def run_once(self):
        with self.sessions() as db:
            job=claim(db)
            if job is None:return None
            job_id,token=job.job_id,job.lease_token
            context=AnalysisContext(job_id,job.corp_code,job.rcept_no,job.as_of,job.input_manifest,
                                    job.input_hash,job.rules_digest,'public:'+job_id,job.attempts)
        task=asyncio.create_task(self.executor(context))
        async def renew():
            while not task.done():
                await asyncio.sleep(max(1,get_settings().ANALYSIS_LEASE_SECONDS/3))
                with self.sessions() as db:alive=heartbeat(db,job_id,token)
                if not alive:
                    task.cancel()
                    raise LostLease('작업 임대 상실')
        keeper=asyncio.create_task(renew())
        try:
            from harness.loop_limits import MAX_SECONDS
            output=await asyncio.wait_for(task,timeout=MAX_SECONDS)
            if not isinstance(output,dict):raise ManualAnalysisError('출력 계약 불일치')
            if output.get('status')=='MANUAL_CHECK':raise ManualAnalysisError('근거 확인 필요')
            if output.get('status')!='DONE':raise ManualAnalysisError('공개 분석 완료가 아님')
            with self.sessions() as db:
                assessment=complete_analysis(db,job_id,token,output['score_result'],output['ai_explanation'])
                result=assessment.assessment_id
            return result
        except asyncio.CancelledError:
            # Lease fencing cancellation is local; process shutdown still propagates.
            if keeper.done() and not keeper.cancelled() and isinstance(keeper.exception(),LostLease):return None
            raise
        except LostLease:
            return None
        except Exception as exc:
            manual=isinstance(exc,(ManualAnalysisError,ValueError,KeyError))
            transient=isinstance(exc,(TransientAnalysisError,TimeoutError,ConnectionError))
            try:
                with self.sessions() as db:
                    fail_job(db,job_id,token,'NEEDS_CHECK' if manual else 'UPSTREAM_ERROR' if transient else 'EXECUTION_ERROR',
                             retryable=transient,manual=manual)
            except LostLease:pass
            return None
        finally:
            keeper.cancel()
            with suppress(asyncio.CancelledError,LostLease):await keeper
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):await task

    async def run_forever(self, poll_seconds=1):
        while True:
            await self.run_once()
            await asyncio.sleep(poll_seconds)


async def serve(executor, once=False):
    import hashlib
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from shared.analysis import enqueue_ready
    from risk_engine.loader import load_rules
    worker=AnalysisWorker(executor)
    while True:
        digest=hashlib.sha256(json.dumps(load_rules(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        day=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
        with get_session() as db:enqueue_ready(db,day,digest)
        await worker.run_once()
        if once:return
        await asyncio.sleep(1)
