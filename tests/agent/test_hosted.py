"""R4 host contexts -> real R2 graphs; fake model/tools, injected saver only."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.hosted import build_runtime
from agent.ports import AgentError
from shared.schemas import ToolResponse
from test_contract_integration import setup, ORANGE
from test_rag_lifecycle import Tools


def public_context(w):
    return SimpleNamespace(rcept_no=ORANGE, execution_context=w.ctx.model_dump())


def review_context(w, **changes):
    values = dict(review_id='review-contract', tenant_id='org-a', assessment_id='public-contract',
                  thread_id='review:review-contract', version=1,
                  public_result=deepcopy(w.response), explanation='공시 근거에 따라 담당 PB 확인이 필요합니다.',
                  customers=({'customer_id': 'C001', 'eval_amount': 1000},
                             {'customer_id': 'C002', 'eval_amount': 500}),
                  existing_drafts={}, decisions={}, rewrite_customers=())
    return SimpleNamespace(**{**values, **changes})


def host(w, saver=None):
    tools = Tools(w.deps.tools)
    return build_runtime(replace(w.deps, tools=tools), saver or InMemorySaver()), tools


def test_factory_uses_injected_saver_and_requires_approved_bindings():
    w = setup(); saver = InMemorySaver()
    runtime, _ = host(w, saver)
    assert runtime.runtime.graph.checkpointer is saver
    assert runtime.review_graph.checkpointer is saver
    with pytest.raises(ValueError, match='UnifiedBindings'):
        build_runtime(replace(w.deps, unified=None), saver)
    with pytest.raises(ValueError, match='checkpointer'):
        build_runtime(w.deps, None)


def test_public_candidate_is_not_saved_by_legacy_store_or_contact_tools():
    w = setup(); runtime, tools = host(w)
    result = asyncio.run(runtime.analyze_public(public_context(w)))
    assert result['status'] == 'DONE' and result['score_result']['stage'] == 'ORANGE'
    assert w.store.saves == [] and w.store.tasks == {}
    assert set(result) == {'status', 'score_result', 'ai_explanation'}
    assert not {'get_holders', 'save_assessment', 'create_contact_task'} & {n for n, _ in tools.calls}


def test_public_manual_failure_does_not_publish():
    w = setup(); w.envelope['availability'] = 'UNAVAILABLE'
    runtime, _ = host(w)
    assert asyncio.run(runtime.analyze_public(public_context(w))) == {'status': 'MANUAL_CHECK'}
    assert not w.store.saves


def test_public_transient_failure_is_mapped_to_worker_retry_exception():
    w = setup(); inner = w.deps.tools
    class TimeoutTools:
        async def call(self, name, args):
            if name == 'get_disclosure_facts':
                return ToolResponse(ok=False, error={'code': 'TIMEOUT', 'message': 'offline timeout'})
            return await inner.call(name, args)
    runtime = build_runtime(replace(w.deps, tools=TimeoutTools()), InMemorySaver())
    with pytest.raises(TimeoutError, match='일시적'):
        asyncio.run(runtime.analyze_public(public_context(w)))
    assert not w.store.saves


def test_public_worker_context_is_not_accepted_as_private():
    w = setup(); runtime, _ = host(w)
    ctx = public_context(w)
    ctx.execution_context.update(kind='review', tenant_id='org-a')
    with pytest.raises(ValueError, match='공개 실행'):
        asyncio.run(runtime.analyze_public(ctx))


def test_review_returns_only_safe_drafts_and_reuses_same_checkpoint():
    async def run():
        w = setup(); runtime, tools = host(w)
        context = review_context(w)
        a = await runtime.draft_review(context)
        calls = len(w.model_calls)
        b = await runtime.draft_review(context)
        assert a == b and len(w.model_calls) == calls
        assert set(a) == {'drafts'} and set(a['drafts']) == {'C001', 'C002'}
        assert all('{고객명}' in v for v in a['drafts'].values())
        assert not w.store.saves and not w.store.tasks
        assert all(n == 'search_reference' for n, _ in tools.calls)
        for prompt, payload in w.model_calls:
            assert prompt in {'draft_message', 'rewrite_message'}
            assert 'orange_score' not in str(payload) and 'total_score' not in str(payload)
    asyncio.run(run())


def test_review_rewrite_keeps_already_decided_message_unchanged():
    w = setup(); runtime, _ = host(w)
    approved = '{고객명}님, 이미 확인한 공시 내용을 담당 PB가 안내드립니다.'
    old = '{고객명}님, 공시 관련 안내입니다.'
    context = review_context(w, existing_drafts={'C001': approved, 'C002': old},
                             decisions={'C001': {'action': 'approve'}}, rewrite_customers=('C002',))
    result = asyncio.run(runtime.draft_review(context))
    assert result['drafts']['C001'] == approved
    assert w.model_calls[0][0] == 'rewrite_message'
    assert [c['customer_id'] for c in w.model_calls[0][1]['customers']] == ['C002']


def test_review_tenant_and_claim_version_have_separate_checkpoint_identity():
    async def run():
        w = setup(); runtime, _ = host(w)
        for tenant, version in [('org-a', 1), ('org-b', 1), ('org-a', 2)]:
            await runtime.draft_review(review_context(w, tenant_id=tenant, version=version))
        assert sum(p == 'draft_message' for p, _ in w.model_calls) == 3
    asyncio.run(run())


@pytest.mark.parametrize('changes', [
    {'existing_drafts': {'OTHER': 'unrelated'}},
    {'decisions': {'C001': {'action': 'approve'}}},
    {'rewrite_customers': ('OTHER',)},
    {'customers': ({'customer_id': 'C001', 'eval_amount': 1}, {'customer_id': 'C001', 'eval_amount': 2})},
    {'public_result': {'stage': 'WATCH'}},
    {'explanation': '내부 점수는 10점입니다.'},
])
def test_invalid_review_context_is_rejected_before_generation(changes):
    w = setup(); runtime, _ = host(w)
    with pytest.raises(ValueError):
        asyncio.run(runtime.draft_review(review_context(w, **changes)))
    assert not w.model_calls


def test_unsafe_review_stops_after_bounded_rewrite_and_returns_no_drafts():
    w = setup(); inner = w.deps.model
    class Model:
        async def generate(self, prompt, schema, payload):
            if prompt in ('draft_message', 'rewrite_message'):
                w.model_calls.append((prompt, deepcopy(payload)))
                return schema.model_validate({'drafts': {c['customer_id']: '{고객명}님, 지금 매도하세요.'
                                                         for c in payload['customers']}})
            return await inner.generate(prompt, schema, payload)
    runtime = build_runtime(replace(w.deps, model=Model()), InMemorySaver())
    with pytest.raises(AgentError, match='안내문 검증'):
        asyncio.run(runtime.draft_review(review_context(w)))
    assert len(w.model_calls) == 3 and not w.store.tasks


def test_r4_sqlite_lifecycle_can_host_the_r2_factory(tmp_path):
    from agent.adapters.executors import open_executors
    async def run():
        w = setup()
        async with open_executors(build_runtime, replace(w.deps, tools=Tools(w.deps.tools)),
                                  tmp_path / 'hosted.db') as ports:
            assert (await ports.public(public_context(w)))['status'] == 'DONE'
            assert set((await ports.review(review_context(w)))['drafts']) == {'C001', 'C002'}
    asyncio.run(run())
