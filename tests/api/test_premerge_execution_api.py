from fastapi.testclient import TestClient
from api.main import app
from api.auth import CurrentPB,get_current_pb
from shared.models import PB,Disclosure
import shared.db as db_module
from shared.analysis import mark_ready
from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
from tests.business.test_premerge_execution import publish


def test_job_api_requires_handoff_and_preserves_internal_boundary(seeded_db):
    app.dependency_overrides[get_current_pb]=lambda:CurrentPB(pb_id='P01',name='fixture',role='ADMIN')
    try:
        with TestClient(app) as client:
            body={'rcept_no':'20250321802001','as_of':'2025-03-21'}
            assert client.post('/analysis/jobs',json=body).status_code==409
            with db_module.get_session() as db:mark_ready(db,body['rcept_no'])
            response=client.post('/analysis/jobs',json=body)
            assert response.status_code==202,response.text
            job=response.json()['job_id']
            status=client.get('/analysis/jobs/'+job).json()
            assert status['status']=='PENDING'
            assert 'input_manifest' not in status and 'rules_digest' not in status
    finally:app.dependency_overrides.clear()


def test_review_api_returns_only_owned_drafts_and_unwired_run_is_explicit(seeded_db):
    aid=publish()
    app.dependency_overrides[get_current_pb]=lambda:CurrentPB(pb_id='P01',name='fixture')
    try:
        with TestClient(app) as client:
            response=client.post('/public-assessments/'+aid+'/reviews')
            assert response.status_code==201,response.text
            review=response.json()['review_id']
            result=client.get('/reviews/'+review).json()
            assert result['stage']=='YELLOW'
            assert not any(key in result for key in ('total_score','yellow_score','rules_digest','input_manifest'))
            assert client.post('/reviews/'+review+'/run').status_code==503
            app.dependency_overrides[get_current_pb]=lambda:CurrentPB(pb_id='other',name='fixture',tenant_id='other')
            assert client.get('/reviews/'+review).status_code==404
    finally:app.dependency_overrides.clear()
