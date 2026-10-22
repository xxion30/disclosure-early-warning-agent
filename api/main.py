"""FastAPI 앱, 포트 8000. 실행: uv run uvicorn api.main:app --reload --port 8000"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import auth
from api.graph_adapter import close_graph_adapter, get_graph_adapter
from api.routes import alerts, customers, events, runs
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
    init_db()  # 테이블이 없으면 만든다. 데이터 적재는 scripts/data/seed_db.py (D)
    app.state.queue = None
    if get_settings().QUEUE_ENABLED:
        await start_queue(app)  # RabbitMQ가 없으면 여기서 실패한다 — docker compose up -d rabbitmq
    yield
    await stop_queue(app)
    await close_graph_adapter()  # 체크포인트 DB 연결 정리


app = FastAPI(title="DARTCH API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite 개발 서버
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(runs.router)
app.include_router(events.router)
app.include_router(alerts.router)
app.include_router(customers.router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
