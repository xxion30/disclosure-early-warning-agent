"""FastAPI 앱, 포트 8000. 실행: uv run uvicorn api.main:app --reload --port 8000"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import auth
from api.routes import alerts, approvals, events, runs, tasks
from shared.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # 테이블이 없으면 만든다. 데이터 적재는 scripts/seed_db.py (D)
    yield


app = FastAPI(title="공시 조기경보 PB API", lifespan=lifespan)

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
app.include_router(approvals.router)
app.include_router(tasks.router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
