"""Exercise R4's opt-in wiring to R3's real dispatcher, with no DART calls."""
import asyncio

import pytest

from collector import jobs
from shared.config import get_settings
from shared.models import CollectCursor, CollectRun, PB


@pytest.fixture
def enabled_dispatch(monkeypatch):
    monkeypatch.setenv('COLLECT_DISPATCH_ENABLED', 'true')
    monkeypatch.setenv('QUEUE_ENABLED', 'false')
    monkeypatch.setenv('PHASE1_RUNTIME_FACTORY', '')
    get_settings.cache_clear()
    calls = []
    completed = asyncio.Event()

    async def collect(run_id, stock_codes):
        calls.append((run_id, stock_codes))
        completed.set()

    # Keep dispatch_collection itself real; replace only the external collection.
    monkeypatch.setattr(jobs, 'run_collection', collect)
    yield calls, completed
    get_settings.cache_clear()


def test_enabled_collection_connects_real_dispatcher_and_committed_reservation(
    enabled_dispatch, client, auth_headers, session_factory,
):
    calls, completed = enabled_dispatch
    assert client.app.state.collect_dispatcher is jobs.dispatch_collection
    with session_factory() as db:
        db.get(PB, 'P01').role = 'ADMIN'
    response = client.post('/collect/run', headers=auth_headers)
    assert response.status_code == 202, response.text
    run_id = response.json()['run_id']

    async def wait_for_background():
        await asyncio.wait_for(completed.wait(), timeout=2)

    client.portal.call(wait_for_background)
    # A new deployment has holdings before Company/corp_code resolution. Do not
    # drop those stocks at the API boundary; R3 resolves/excludes them with DART.
    stocks = calls[0][1]
    assert len(calls) == 1 and calls[0][0] == run_id
    assert '001570' in stocks and '069500' in stocks and len(stocks) > 1
    with session_factory() as db:
        assert db.get(CollectRun, run_id).stock_codes == stocks
        assert db.get(CollectCursor, '001570').active_run_id == run_id
