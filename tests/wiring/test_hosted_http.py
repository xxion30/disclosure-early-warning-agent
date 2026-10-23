"""Real HTTP handlers -> public/review transactions; no provider/accuracy claim."""
from fastapi.testclient import TestClient
from sqlalchemy import select
import shared.db as db_module
from shared.config import get_settings
from shared.models import PublicAssessment, OrganizationReview, ContactTask
from mcp_servers.business.reviews import create_review, claim_review, save_drafts
from tests.business.test_premerge_execution import publish, add_second_org


def test_hosted_alert_and_approval_use_only_the_callers_organization(seeded_db, monkeypatch):
    from api.main import app
    from api.auth import CurrentPB, get_current_pb
    from api.deps import get_db
    from api.graph_adapter import get_graph_adapter
    monkeypatch.setenv('PHASE1_RUNTIME_FACTORY', 'agent.hosted:build_runtime')
    get_settings.cache_clear()
    aid = publish()
    with db_module.get_session() as db:
        public = db.get(PublicAssessment, aid)
        legacy_id = public.legacy_assessment_id
        from shared.models import Company
        stock = db.get(Company, public.corp_code).stock_code
        add_second_org(db, stock)
        review = create_review(db, 'org-two', aid)
        rid = review.review_id
        claimed = claim_review(db, rid, 'org-two')
        save_drafts(db, rid, 'org-two', claimed.lease_token,
                    {'customer-two':'{고객명}님, 공시 내용을 확인하시고 PB와 상담해 주세요.'})
        version = review.version
    who = [CurrentPB(pb_id='pb-two', name='protocol', tenant_id='org-two')]
    def database():
        with db_module.get_session() as db:
            yield db
    class NoLegacyGraph:
        async def get_state(self, *args):
            raise AssertionError('Hosted HTTP must not read a legacy checkpoint')
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.update({get_db:database, get_current_pb:lambda:who[0],
                                     get_graph_adapter:lambda:NoLegacyGraph()})
    try:
        client = TestClient(app)
        detail = client.get(f'/alerts/{legacy_id}')
        assert detail.status_code == 200, detail.text
        data = detail.json()
        assert data['review_backend'] == 'organization' and data['review_id'] == rid
        assert {c['customer_id'] for c in data['customers']} == {'customer-two'}
        assert data['customers'][0]['mine'] and 'total_score' not in data
        assert all('points' not in row for row in data['reasons'])
        rows = client.get('/alerts').json()
        assert next(row for row in rows if row['assessment_id'] == legacy_id)['review_id'] == rid
        assert client.get(f'/reviews/{rid}').json()['drafts'].keys() == {'customer-two'}
        decision = {'expected_version':version,'decisions':[{'customer_id':'customer-two','action':'approve'}]}
        assert client.post(f'/reviews/{rid}/decisions',json=decision).status_code == 200
        assert client.post(f'/reviews/{rid}/decisions',json=decision).status_code == 200
        with db_module.get_session() as db:
            assert len(db.scalars(select(ContactTask).where(ContactTask.review_id == rid)).all()) == 1
        who[0] = CurrentPB(pb_id='P01', name='protocol')
        assert client.get(f'/reviews/{rid}').status_code == 404
        assert client.post(f'/reviews/{rid}/decisions',json=decision).status_code == 403
        # New hosted service cannot accidentally resume an old receipt-only Graph.
        assert client.post('/runs', json={'rcept_no':'20250321802001'}).status_code == 409
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
        get_settings.cache_clear()
