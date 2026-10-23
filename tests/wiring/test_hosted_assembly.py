"""R4 assembly -> real R2 hosted graphs -> actual business tools and DB.

The model/RAG are local protocol doubles; citations use existing stored text.
No DART/model network call or prediction-accuracy claim is made.
"""
import asyncio
import hashlib
import pytest
from sqlalchemy import select

import shared.db as db_module
from shared.config import get_settings
from shared.models import Disclosure, AnalysisJob, PublicAssessment, OrganizationReview, ContactTask
from shared.analysis import mark_ready, enqueue, claim, fail_job, LostLease
from shared.facts import store_facts
from agent.adapters.assembly import build_dependencies
from agent.adapters.executors import open_executors
from agent.hosted import build_runtime
from api.worker.analysis import AnalysisWorker
from api.review_executor import run_review
from shared.schemas import ToolResponse

RCEPT = '20250321802001'
DAY = '2025-03-21'


class LocalTools:
    def __init__(self):
        self.calls = []

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == 'search_disclosure_text':
            with db_module.get_session() as db:
                row = db.get(Disclosure, arguments['rcept_no'])
                text = row.body_text
            return ToolResponse(ok=True, data={'content_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'normalization_version': 'protocol-fixture', 'truncated': False,
                'chunks': [{'source': 'stored-document', 'rcept_no': arguments['rcept_no'], 'location': 'stored-body', 'text': text}]})
        if name in ('release_disclosure_index', 'search_reference'):
            return ToolResponse(ok=True, data={'chunks': []})
        import importlib
        function = getattr(importlib.import_module('mcp_servers.business.tools.' + name), name)
        return ToolResponse.model_validate(function(**arguments))


class LocalModel:
    def __init__(self):
        self.calls = []

    async def generate(self, prompt, schema, payload):
        self.calls.append(prompt)
        if prompt == 'select_tools':
            return schema.model_validate({'requests': []})
        if prompt == 'extract_flags':
            chunk = payload['chunks'][0]
            evidence = {k: chunk[k] for k in ('source', 'rcept_no', 'location')}
            evidence['quote'] = chunk['text'][:80]
            return schema.model_validate({'flags': [
                {'item': route['item'], 'flag': flag, 'value': '거절' if flag == 'opinion' else False,
                 'evidence': [evidence]}
                for route in payload['routes'] for flag in route['text_flags']]})
        if prompt == 'explain_risk':
            return schema.model_validate({'items': [
                {'breakdown_index': i, 'explanation': '감사 공시 근거를 확인해 주세요.', 'evidence': row['evidence']}
                for i, row in enumerate(payload['score_result']['breakdown'])]})
        if prompt in ('draft_message', 'rewrite_message'):
            return schema.model_validate({'drafts': {row['customer_id']:
                '{고객명}님, 공시 내용을 확인하시고 담당 PB와 상담해 주세요.' for row in payload['customers']}})
        raise AssertionError(prompt)


def prepare(*, available=True, receipt=RCEPT):
    from risk_engine.loader import load_rules
    # A known-empty facts payload exercises the protocol, not actual API coverage.
    with db_module.get_session() as db:
        disclosure = db.get(Disclosure, receipt)
        if available:
            store_facts(db, receipt, {'schema_version': '1', 'rcept_no': receipt, 'corp_code': disclosure.corp_code,
                                   'collected_at': '2025-03-21T09:00:00+09:00', 'facts': []})
        else:
            disclosure.structured_facts = None
        mark_ready(db, receipt)
        import json
        rules = hashlib.sha256(json.dumps(load_rules(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return enqueue(db, receipt, DAY, rules).job_id


def hosted_settings():
    return get_settings().model_copy(update={'PHASE1_RUNTIME_FACTORY': 'agent.hosted:build_runtime'})


def test_real_assembly_publication_and_private_drafts_are_separate(seeded_db, tmp_path):
    job_id = prepare()
    tools, model = LocalTools(), LocalModel()

    async def run():
        deps = await build_dependencies(hosted_settings(), tools=tools, model=model)
        assert deps.unified is not None
        async with open_executors(build_runtime, deps, tmp_path / 'hosted.db') as ports:
            aid = await AnalysisWorker(ports.public).run_once()
            assert aid is not None
            with db_module.get_session() as db:
                public = db.get(PublicAssessment, aid)
                assert public.result['stage'] == 'YELLOW'
                job = db.get(AnalysisJob, job_id)
                assert job.status == 'DONE' and job.execution_budget['model_turns'] == 3
                assert db.scalar(select(ContactTask)) is None
                review = db.scalar(select(OrganizationReview).where(OrganizationReview.assessment_id == aid))
                rid, tenant = review.review_id, review.tenant_id
            before = len(tools.calls)
            await run_review(ports.review, rid, tenant)
            assert all(name == 'search_reference' for name, _ in tools.calls[before:])
            with db_module.get_session() as db:
                review = db.get(OrganizationReview, rid)
                assert review.status == 'WAITING_APPROVAL' and review.drafts
                assert review.execution_budget['model_turns'] == 1
                assert db.scalar(select(ContactTask)) is None
                # A fresh claim/checkpoint must continue the same private budget.
                review.status = 'MANUAL_CHECK'
                review.drafts = {}
            await run_review(ports.review, rid, tenant)
            with db_module.get_session() as db:
                review = db.get(OrganizationReview, rid)
                assert review.execution_budget['model_turns'] == 2
                assert review.status == 'WAITING_APPROVAL'
            assert not {'save_assessment', 'create_contact_task', 'purge_disclosure_text'} & {n for n, _ in tools.calls}
    asyncio.run(run())


def test_red_integer_zero_and_wire_float_zero_publish_the_same_rule_result(seeded_db, tmp_path):
    receipt = '20220103900001'  # Actual stored Osstem embezzlement filing.
    job_id=prepare(receipt=receipt)
    class CopiedReasonModel(LocalModel):
        async def generate(self,prompt,schema,payload):
            response=await super().generate(prompt,schema,payload)
            if prompt == 'extract_flags':
                for flag in response.flags:
                    if flag.flag == 'reason':
                        flag.value=flag.evidence[0].quote
            return response
    async def run():
        deps=await build_dependencies(hosted_settings(),tools=LocalTools(),model=CopiedReasonModel())
        async with open_executors(build_runtime,deps,tmp_path/'red.db') as ports:
            aid=await AnalysisWorker(ports.public).run_once()
            assert aid is not None
        with db_module.get_session() as db:
            assert db.get(PublicAssessment,aid).result['stage'] == 'RED'
            assert db.get(AnalysisJob,job_id).status == 'DONE'
    asyncio.run(run())


def test_ledger_survives_retry_and_rejects_the_old_owner(seeded_db):
    from agent.adapters.budgets import ledger_for, record_budget
    from agent.execution import Execution, current_execution
    from types import SimpleNamespace
    job_id = prepare()
    with db_module.get_session() as db:
        job = claim(db)
        context = SimpleNamespace(job_id=job_id, lease_token=job.lease_token)

    async def use():
        token = current_execution.set(Execution(120, 8))
        try:
            with ledger_for(context, 'public'):
                await record_budget({})
                await current_execution.get().reserve_model()
        finally:
            current_execution.reset(token)
    asyncio.run(use())
    with db_module.get_session() as db:
        assert db.get(AnalysisJob, job_id).execution_budget['model_turns'] == 1
        from datetime import timedelta
        from shared.analysis import utcnow
        fail_job(db, job_id, context.lease_token, 'UPSTREAM_ERROR', retryable=True,
                 now=utcnow() - timedelta(seconds=60))
    with pytest.raises(LostLease):
        with ledger_for(context, 'public'):
            pass
    with db_module.get_session() as db:
        job = claim(db)
        assert job.attempts == 2
        context = SimpleNamespace(job_id=job_id, lease_token=job.lease_token)
    asyncio.run(use())
    with db_module.get_session() as db:
        assert db.get(AnalysisJob, job_id).execution_budget['model_turns'] == 2


def test_exhausted_budget_stops_before_model_and_releases_search(seeded_db, tmp_path):
    job_id = prepare()
    with db_module.get_session() as db:
        db.get(AnalysisJob, job_id).execution_budget = {'model_turns': 8, 'consumed_seconds': 0, 'repeated_calls': {}}
    tools, model = LocalTools(), LocalModel()
    async def run():
        deps = await build_dependencies(hosted_settings(), tools=tools, model=model)
        async with open_executors(build_runtime, deps, tmp_path / 'budget.db') as ports:
            assert await AnalysisWorker(ports.public).run_once() is None
    asyncio.run(run())
    assert not model.calls
    assert 'release_disclosure_index' in [name for name, _ in tools.calls]
    with db_module.get_session() as db:
        assert db.get(AnalysisJob, job_id).status == 'MANUAL_CHECK'


def test_unavailable_facts_and_verified_financial_sources_complete_public_analysis(seeded_db, tmp_path):
    """Financial numbers here are protocol fixtures, not issuer accuracy labels."""
    from shared.models import FinancialSnapshot
    from mcp_servers.business.tools.verify_financial_evidence import verify_financial_evidence
    from copy import deepcopy
    with db_module.get_session() as db:
        corp = db.get(Disclosure, RCEPT).corp_code
        db.add(FinancialSnapshot(corp_code=corp, rcept_no=RCEPT, period='2024', period_kind='ANNUAL',
            basis='CONSOLIDATED', revision=1, created_at=DAY,
            indicators={'CAPITAL_IMPAIRMENT': {'status':'OK','detail':{'impairment_pct':100}}},
            sources=[{'api':'protocol-fixture','accounts':[]}]))
    job_id = prepare(available=False)
    tools, model = LocalTools(), LocalModel()

    async def run():
        deps = await build_dependencies(hosted_settings(), tools=tools, model=model)
        async with open_executors(build_runtime, deps, tmp_path/'financial.db') as ports:
            assert await AnalysisWorker(ports.public).run_once() is not None
    asyncio.run(run())
    with db_module.get_session() as db:
        public = db.scalar(select(PublicAssessment).where(PublicAssessment.job_id == job_id))
        financial = [ev for row in public.result['breakdown'] for ev in row['evidence'] if ev.get('kind') == 'financial']
    assert financial and all('quote' not in ev for ev in financial)
    assert verify_financial_evidence(corp, DAY, financial)['ok']
    forged = deepcopy(financial)
    forged[0]['values']['impairment_pct'] = 0
    assert not verify_financial_evidence(corp, DAY, forged)['ok']


def test_missing_financial_indicator_does_not_hide_confirmed_risk(seeded_db, tmp_path):
    """Protocol-only financial fixtures exercise missing-data handling, not accuracy."""
    from shared.models import FinancialSnapshot, RiskState
    from shared.presentation import INCOMPLETE_FINANCIAL_NOTICE
    with db_module.get_session() as db:
        corp = db.get(Disclosure, RCEPT).corp_code
        db.add(FinancialSnapshot(corp_code=corp, rcept_no=RCEPT, period='2024', period_kind='ANNUAL',
            basis='CONSOLIDATED', revision=1, created_at=DAY,
            indicators={'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':100}},
                        'INTEREST_BURDEN':{'status':'MISSING','detail':{}}},
            sources=[{'api':'protocol-fixture','accounts':[]}]))
    job_id = prepare(available=False)
    tools, model = LocalTools(), LocalModel()
    async def run():
        deps = await build_dependencies(hosted_settings(), tools=tools, model=model)
        async with open_executors(build_runtime, deps, tmp_path/'partial.db') as ports:
            aid = await AnalysisWorker(ports.public).run_once()
            assert aid is not None
            with db_module.get_session() as db:
                public = db.get(PublicAssessment, aid)
                assert public.result['manual_check'] and public.result['stage'] == 'YELLOW'
                assert INCOMPLETE_FINANCIAL_NOTICE in public.explanation
                assert db.get(RiskState,corp).manual_check
                assert db.get(AnalysisJob,job_id).status == 'MANUAL_CHECK'
                assert db.get(Disclosure,RCEPT).processed_at is None
                review = db.scalar(select(OrganizationReview).where(OrganizationReview.assessment_id==aid))
                rid = review.review_id
            await run_review(ports.review, rid, 'default')
            with db_module.get_session() as db:
                assert db.get(OrganizationReview,rid).status == 'WAITING_APPROVAL'
    asyncio.run(run())


def test_only_missing_data_is_not_published_as_a_successful_no_risk_assessment(seeded_db, tmp_path):
    from shared.models import FinancialSnapshot, RiskState
    from shared.presentation import INCOMPLETE_FINANCIAL_NOTICE
    with db_module.get_session() as db:
        corp = db.get(Disclosure,RCEPT).corp_code
        db.add(FinancialSnapshot(corp_code=corp,rcept_no=RCEPT,period='2024',period_kind='ANNUAL',
            basis='CONSOLIDATED',revision=1,created_at=DAY,
            indicators={'INTEREST_BURDEN':{'status':'MISSING','detail':{}}},
            sources=[{'api':'protocol-fixture','accounts':[]}]))
    job_id = prepare(available=False)
    class BenignProtocolModel(LocalModel):
        async def generate(self,prompt,schema,payload):
            response=await super().generate(prompt,schema,payload)
            if prompt == 'extract_flags':
                for flag in response.flags:
                    if flag.flag == 'opinion':
                        flag.value='적정'
            return response
    async def run():
        deps=await build_dependencies(hosted_settings(),tools=LocalTools(),model=BenignProtocolModel())
        async with open_executors(build_runtime,deps,tmp_path/'missing-only.db') as ports:
            aid=await AnalysisWorker(ports.public).run_once()
            assert aid is not None
        with db_module.get_session() as db:
            public=db.get(PublicAssessment,aid)
            assert public.result['stage'] == 'NONE' and public.result['manual_check']
            assert INCOMPLETE_FINANCIAL_NOTICE in public.explanation
            assert db.get(AnalysisJob,job_id).status == 'MANUAL_CHECK'
            assert db.get(Disclosure,RCEPT).processed_at is None
            assert db.scalar(select(OrganizationReview)) is None
    asyncio.run(run())
