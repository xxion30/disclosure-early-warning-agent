from api.tenancy import require_legacy_runtime
"""POST /runs — 공시 1건 처리 시작, GET /runs/{rcept_no} — 현재 진행 상태.

QUEUE_ENABLED=true면 바로 실행하지 않고 RabbitMQ에 넣는다 (가산점). 소비자가 꺼내 실행하고,
SSE는 그 전부터 붙어 있을 수 있다(queued 이벤트 → 실행 이벤트).
- POST /runs/batch  여러 건 한꺼번에 (실적 시즌)
- GET  /runs/queue  큐 길이와 최근 처리 결과
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from api.auth import CurrentPB, get_current_pb
from api.run_manager import RunConflict, RunManager, get_run_manager
from api.worker.queue import MAIN_QUEUE, QueueBackend

router = APIRouter(prefix="/runs", tags=["runs"])

RCEPT_NO_PATTERN = r"^\d{14}$"  # DART 접수번호 14자리


class RunRequest(BaseModel):
    rcept_no: str = Field(pattern=RCEPT_NO_PATTERN)


def get_queue(request: Request) -> QueueBackend | None:
    """lifespan이 QUEUE_ENABLED일 때만 연결해 둔다. 없으면 None (바로 실행 모드)."""
    return getattr(request.app.state, "queue", None)


class BatchRequest(BaseModel):
    rcept_nos: list[str] = Field(min_length=1, max_length=500)

    @field_validator("rcept_nos")
    @classmethod
    def check(cls, v: list[str]) -> list[str]:
        import re

        bad = [x for x in v if not re.fullmatch(RCEPT_NO_PATTERN, x)]
        if bad:
            raise ValueError(f"접수번호 형식(14자리)이 아닙니다: {', '.join(bad[:5])}")
        return list(dict.fromkeys(v))  # 중복 제거, 순서 유지


class RunStarted(BaseModel):
    rcept_no: str
    status: str = "RUNNING"
    events_url: str


class RunStatus(BaseModel):
    rcept_no: str
    running: bool
    last_event: str | None
    events: list[dict]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=RunStarted)
async def start_run(
    body: RunRequest,
    pb: CurrentPB = Depends(get_current_pb),
    manager: RunManager = Depends(get_run_manager),
    queue: QueueBackend | None = Depends(get_queue),
) -> RunStarted:
    require_legacy_runtime(pb)
    try:
        if queue is None:
            manager.start(body.rcept_no)
        else:
            manager.mark_queued(body.rcept_no)
    except RunConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if queue is not None:
        await queue.publish(MAIN_QUEUE, {"rcept_no": body.rcept_no, "attempt": 1})
    return RunStarted(rcept_no=body.rcept_no, status="QUEUED" if queue else "RUNNING",
                      events_url=f"/runs/{body.rcept_no}/events")


@router.post("/batch", status_code=status.HTTP_202_ACCEPTED)
async def start_batch(
    body: BatchRequest,
    pb: CurrentPB = Depends(get_current_pb),
    manager: RunManager = Depends(get_run_manager),
    queue: QueueBackend | None = Depends(get_queue),
) -> dict:
    """여러 공시를 한꺼번에 큐에 넣는다. 이미 실행 중·처리된 공시는 건너뛴다."""
    require_legacy_runtime(pb)
    if queue is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "QUEUE_ENABLED=false — 일괄 처리는 큐 모드에서만 됩니다.")
    queued, skipped = [], []
    for i, rcept_no in enumerate(body.rcept_nos, 1):
        try:
            manager.mark_queued(rcept_no, position=i)
        except RunConflict:
            skipped.append(rcept_no)
            continue
        await queue.publish(MAIN_QUEUE, {"rcept_no": rcept_no, "attempt": 1})
        queued.append(rcept_no)
    return {"queued": queued, "skipped": skipped}


@router.get("/queue")
async def queue_status(
    request: Request,
    pb: CurrentPB = Depends(get_current_pb),
    queue: QueueBackend | None = Depends(get_queue),
) -> dict:
    require_legacy_runtime(pb)
    if queue is None:
        return {"enabled": False}
    consumer = getattr(request.app.state, "consumer", None)
    recent = consumer.processed[-20:] if consumer else []
    return {"enabled": True, "queues": await queue.stats(),
            "recent": [{"rcept_no": r, "attempt": a, "result": res} for r, a, res in recent]}


@router.get("/{rcept_no}", response_model=RunStatus)
async def get_run(
    rcept_no: str,
    pb: CurrentPB = Depends(get_current_pb),
    manager: RunManager = Depends(get_run_manager),
) -> RunStatus:
    """SSE가 끊겼을 때 화면이 현재 단계를 다시 그리는 용도."""
    require_legacy_runtime(pb)
    ch = manager.get(rcept_no)
    if ch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "이 서버에서 실행한 기록이 없습니다.")
    return RunStatus(rcept_no=rcept_no, running=ch.running, last_event=ch.last_event, events=ch.history)
