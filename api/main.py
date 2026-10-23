"""FastAPI 앱, 포트 8000. 실행: uv run uvicorn api.main:app --reload --port 8000"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import auth
from api.graph_adapter import close_graph_adapter, get_graph_adapter
from api.routes import execution, alerts, approvals, customers, events, runs, tasks, states, collect
from api.run_manager import get_run_manager
from shared.config import get_settings
from shared.db import init_db

log = logging.getLogger("api")


async def start_queue(app: FastAPI) -> None:
    """QUEUE_ENABLED=true: RabbitMQ에 붙고 소비자를 API 프로세스 안에서 돌린다 (api/worker/consumer.py)."""
    from api.worker.consumer import DisclosureConsumer
    from api.worker.queue import AioPikaBackend

    backend = await AioPikaBackend.connect(get_settings().RABBITMQ_URL)
    consumer = DisclosureConsumer(get_run_manager(), get_graph_adapter(), backend)
    app.state.queue = backend
    app.state.consumer = consumer
    app.state.consumer_task = asyncio.create_task(consumer.run_forever(), name="queue-consumer")
    log.info("queue: RabbitMQ 연결, 소비자 시작")


async def stop_queue(app: FastAPI) -> None:
    task = getattr(app.state, "consumer_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    backend = getattr(app.state, "queue", None)
    if backend is not None:
        await backend.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_settings().PHASE1_RUNTIME_FACTORY and get_settings().QUEUE_ENABLED:
        raise RuntimeError('Phase 1 DB 실행기에서는 QUEUE_ENABLED=false가 필요합니다.')
    init_db()  # 테이블이 없으면 만든다. 데이터 적재는 scripts/data/seed_db.py (D)
    app.state.queue = None
    if get_settings().QUEUE_ENABLED:
        await start_queue(app)  # RabbitMQ가 없으면 여기서 실패한다 — docker compose up -d rabbitmq
    # Explicit injection only: R2 public/review ports may be configured by the host.
    app.state.collect_dispatcher = None
    if get_settings().COLLECT_DISPATCH_ENABLED:
        from collector.jobs import dispatch_collection
        app.state.collect_dispatcher = dispatch_collection
    execution_cm=None
    analysis_task=None
    if get_settings().PHASE1_RUNTIME_FACTORY:
        import importlib
        from agent.adapters.assembly import build_dependencies
        from agent.adapters.executors import open_executors
        from api.worker.analysis import serve
        module,name=get_settings().PHASE1_RUNTIME_FACTORY.rsplit(':',1)
        factory=getattr(importlib.import_module(module),name)
        deps=await build_dependencies()
        execution_cm=open_executors(factory,deps,get_settings().CHECKPOINT_DB)
        ports=await execution_cm.__aenter__()
        app.state.review_executor=ports.review
        analysis_task=asyncio.create_task(serve(ports.public),name='db-analysis-worker')
    try:
        yield
    finally:
        if analysis_task is not None:
            analysis_task.cancel()
            from contextlib import suppress
            with suppress(asyncio.CancelledError):await analysis_task
        if execution_cm is not None:await execution_cm.__aexit__(None,None,None)
    await stop_queue(app)
    await close_graph_adapter()  # 체크포인트 DB 연결 정리


app = FastAPI(title="공시 조기경보 PB API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite 개발 서버
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(execution.router)
app.include_router(states.router)
app.include_router(collect.router)
app.include_router(auth.router)
app.include_router(runs.router)
app.include_router(events.router)
app.include_router(alerts.router)
app.include_router(approvals.router)
app.include_router(tasks.router)
app.include_router(customers.router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


from fastapi import Request
from starlette.exceptions import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, headers=exc.headers,
        content={'ok': False, 'error': {'code': {401:'UNAUTHORIZED',403:'FORBIDDEN',404:'NOT_FOUND',409:'CONFLICT',503:'UNAVAILABLE'}.get(exc.status_code,str(exc.status_code)), 'message': str(exc.detail)}, 'detail': exc.detail})

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    from fastapi.encoders import jsonable_encoder
    return JSONResponse(status_code=422, content={'ok': False, 'error': {'code': 'INVALID_INPUT', 'message': '입력 형식이 올바르지 않습니다.'},
        'detail': jsonable_encoder(exc.errors(), custom_encoder={ValueError: str})})
