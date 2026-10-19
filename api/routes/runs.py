"""POST /runs — 공시 1건 처리 시작, GET /runs/{rcept_no} — 현재 진행 상태."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.auth import CurrentPB, get_current_pb
from api.run_manager import RunConflict, RunManager, get_run_manager

router = APIRouter(prefix="/runs", tags=["runs"])

RCEPT_NO_PATTERN = r"^\d{14}$"  # DART 접수번호 14자리


class RunRequest(BaseModel):
    rcept_no: str = Field(pattern=RCEPT_NO_PATTERN)


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
) -> RunStarted:
    # TODO(가산점): QUEUE_ENABLED면 여기서 바로 실행하지 않고 RabbitMQ에 넣는다
    try:
        manager.start(body.rcept_no)
    except RunConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return RunStarted(rcept_no=body.rcept_no, events_url=f"/runs/{body.rcept_no}/events")


@router.get("/{rcept_no}", response_model=RunStatus)
async def get_run(
    rcept_no: str,
    pb: CurrentPB = Depends(get_current_pb),
    manager: RunManager = Depends(get_run_manager),
) -> RunStatus:
    """SSE가 끊겼을 때 화면이 현재 단계를 다시 그리는 용도."""
    ch = manager.get(rcept_no)
    if ch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "이 서버에서 실행한 기록이 없습니다.")
    return RunStatus(rcept_no=rcept_no, running=ch.running, last_event=ch.last_event, events=ch.history)
