"""Deployment readiness must distinguish an HTTP process from a live worker."""
import asyncio
import pytest
from fastapi.testclient import TestClient
from api.main import app
from shared.config import get_settings


def test_ready_detects_a_stopped_worker(client, session_factory, monkeypatch):
    import shared.db
    monkeypatch.setattr(shared.db, 'get_session', session_factory)
    assert client.get('/ready').status_code == 200
    async def stopped():
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        app.state.analysis_task = task
    client.portal.call(stopped)
    assert client.get('/health').status_code == 200
    assert client.get('/ready').status_code == 503


def test_production_rejects_development_credentials_before_starting_services(monkeypatch):
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.setenv('JWT_SECRET','change-me')
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match='JWT_SECRET'):
            with TestClient(app):
                pass
    finally:
        get_settings.cache_clear()
