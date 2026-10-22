"""GET /runs/{rcept_no}/events — 진행 이벤트 SSE (CLAUDE.md 8장 이벤트 이름).

waiting_approval / done / failed 중 하나가 오면 스트림을 닫는다.
화면은 EventSource(`/runs/{rcept_no}/events?token=<JWT>`)로 붙는다.
"""
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from api.auth import CurrentPB, get_current_pb_sse
from api.graph_adapter import Event
from api.run_manager import RunManager, get_run_manager

router = APIRouter(tags=["events"])


def format_sse(event: Event | None) -> str:
    if event is None:
        return ": keep-alive\n\n"
    data = json.dumps(event["data"], ensure_ascii=False)
    return f"event: {event['event']}\ndata: {data}\n\n"


@router.get("/runs/{rcept_no}/events")
async def run_events(
    rcept_no: str,
    pb: CurrentPB = Depends(get_current_pb_sse),
    manager: RunManager = Depends(get_run_manager),
) -> StreamingResponse:
    if manager.get(rcept_no) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "이 서버에서 실행한 기록이 없습니다.")

    async def stream() -> AsyncIterator[str]:
        async for ev in manager.subscribe(rcept_no):
            yield format_sse(ev)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
