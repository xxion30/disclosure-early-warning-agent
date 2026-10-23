"""Actual repository disclosure -> rule tool -> DB -> authenticated score-free state API."""
from fastapi.testclient import TestClient
from sqlalchemy import select
from api.main import app
from api.deps import get_db
from api.auth import create_access_token
from shared.db import get_session
from shared.models import PB
from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
from mcp_servers.business.tools.save_assessment import save_assessment


def test_repository_audit_case_reaches_current_state_api(seeded_db):
    rcept='20250321802001'
    score=calculate_risk_score('00106119',[{'signal_type':'AUDIT_OPINION','rcept_no':rcept,
                                          'detail':{'opinion':'의견거절'}}],'2025-03-21')
    assert score['ok'] and score['data']['stage']=='YELLOW'
    stored=save_assessment('00106119',rcept,score['data']['signals'],score['data'],status='DONE')
    assert stored['ok']
    with get_session() as db:
        token=create_access_token(db.get(PB,'P01'))
    def request_db():
        with get_session() as db:
            yield db
    app.dependency_overrides[get_db]=request_db
    try:
        with TestClient(app) as client:
            headers={'Authorization':f'Bearer {token}'}
            response=client.get('/states/001570',headers=headers)
            assert response.status_code==200
            assert response.json()['stage']=='YELLOW'
            assert 'yellow_score' not in response.text and 'points' not in response.text
    finally:
        app.dependency_overrides.clear()
