"""api 테스트 공통 준비. 실제 data/app.db는 건드리지 않고 메모리 DB와 가짜 Graph를 쓴다."""
import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api.main
from api.auth import hash_password
from api.deps import get_db
from api.graph_adapter import FakeGraphAdapter, get_graph_adapter
from api.main import app
from api.run_manager import RunManager, get_run_manager
from shared.config import ROOT_DIR
from shared.models import PB, Base, Customer
from tests.api.helpers import login


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    seed = json.loads((ROOT_DIR / "data" / "seed" / "customers.json").read_text(encoding="utf-8"))
    with SessionLocal() as s:
        for p in seed["pbs"]:
            s.add(PB(pb_id=p["pb_id"], name=p["name"], email=p["email"], password_hash=hash_password(p["password"])))
        for c in seed["customers"]:
            s.add(Customer(**{k: c[k] for k in ("customer_id", "pb_id", "name", "risk_profile", "total_assets")}))
        s.commit()

    @contextmanager
    def factory():
        with SessionLocal() as s:
            yield s
            s.commit()

    return factory


@pytest.fixture
def adapter(session_factory) -> FakeGraphAdapter:
    return FakeGraphAdapter(delay=0, session_factory=session_factory)


@pytest.fixture
def manager(adapter) -> RunManager:
    return RunManager(adapter)


@pytest.fixture
def client(monkeypatch, session_factory, adapter, manager):
    def override_get_db():
        with session_factory() as s:
            yield s

    monkeypatch.setattr(api.main, "init_db", lambda: None)  # lifespan이 실제 DB를 만들지 않게
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_graph_adapter] = lambda: adapter
    app.dependency_overrides[get_run_manager] = lambda: manager
    # with 블록: 요청 사이에 같은 이벤트 루프를 유지해야 백그라운드 실행이 살아 있다
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client):
    token = login(client).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def p02_headers(client):
    token = login(client, "p02@demo.local").json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
