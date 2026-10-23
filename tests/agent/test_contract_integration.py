"""Contract regressions. Actual PM disclosure text; fake rule replies are NOT labels."""
import asyncio
import copy
from dataclasses import replace
from hashlib import sha256
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.evidence import retained_data, select_chunks, validate_known_evidence
from agent.execution import Execution, current_execution, generate
from agent.harness_llm import recover_llm
from agent.integration import ExecutionContext, UnifiedBindings, UnifiedResult
from agent.llm import StructuredLLM
from agent.ports import AgentError
from agent.runtime import AgentRuntime, open_runtime
from agent.state import ApprovalBatch, PBContext, ToolSelection
from agent.structured_facts import canonical_facts_hash, validate_facts
from shared.schemas import ToolResponse
from test_phase1 import world, ORANGE, actual_disclosure

RULES = sha256(b'control-flow-test-rule-version').hexdigest()


def setup(stage='ORANGE'):
    w = world(stage=stage, score=4)
    row, meta, body = actual_disclosure(ORANGE)
    calls = []
    facts = {'schema_version': '1', 'rcept_no': ORANGE, 'corp_code': row['corp_code'],
             'collected_at': '2026-10-23T09:05:00+09:00',
             'facts': [{'name': 'issue_method', 'dtype': 'string', 'raw': '주주배정후 실권주 일반공모',
                        'value': '주주배정후 실권주 일반공모', 'unit': None, 'status': 'PRESENT', 'reason': None,
                        'source': {'api': 'piicDecsn', 'field': 'ic_mthn', 'rcept_no': ORANGE,
                                   'fetched_at': '2026-10-23T09:05:00+09:00'}}]}
    envelope = {'availability': 'AVAILABLE', 'rcept_no': ORANGE, 'corp_code': row['corp_code'],
                'structured_facts': facts, 'facts_hash': canonical_facts_hash(facts)}
    response = {'stage': stage, 'active_stages': [stage], 'orange_score': 4., 'yellow_score': 0.,
                'composite_ratio': 1., 'sort_value': 1., 'manual_check': False, 'manual_reasons': [],
                'breakdown': [] if stage == 'NONE' else [{'item': '유상증자', 'evidence': [w.ev],
                    'source_chunks': [{**w.ev, 'text': body[:800]}]}],
                'signals': [], 'level': 'Low' if stage == 'NONE' else 'High', 'total_score': 4.,
                'combo_upgraded': False, 'rules_digest': RULES, 'expected_state_version': 2,
                'corp_code': row['corp_code'], 'as_of': row['filed_at'][:10]}
    inner = w.deps.tools
    class Tools:
        async def call(self, name, args):
            calls.append((name, copy.deepcopy(args)))
            if name == 'get_disclosure_facts':
                return ToolResponse(ok=True, data=copy.deepcopy(envelope))
            if name == 'calculate_risk_score':
                return ToolResponse(ok=True, data=copy.deepcopy(response))
            r = await inner.call(name, args)
            if name == 'search_disclosure_text':
                r.data.update(content_sha256=sha256(body.encode()).hexdigest(), normalization_version='fixture-1')
            return r
    options = {'current': True}
    async def current(payload):
        return options['current']
    def prepare(payload):
        # Stand-in for R4's input adapter: values are not scored here.
        return [{'signal_type': 'RIGHTS_ISSUE', 'detail': {}, 'rcept_no': ORANGE,
                 'evidence': payload['evidence']}] if stage != 'NONE' else []
    bindings = UnifiedBindings(classify=(w.deps.phase1.classify if stage != 'NONE' else lambda _: []),
                               prepare_signals=prepare, ensure_current=current)
    deps = replace(w.deps, tools=Tools(), phase1=None, unified=bindings)
    ctx = ExecutionContext(assessment_key='assessment-control', as_of=response['as_of'],
                           rules_version=RULES, input_version='whole-input-manifest-control',
                           facts_version=envelope['facts_hash'], run_id='run-control')
    w.deps, w.ctx, w.facts, w.response, w.extra_calls, w.integration_options = deps, ctx, facts, response, calls, options
    w.envelope = envelope
    return w


def public(w):
    return asyncio.run(AgentRuntime(w.deps, InMemorySaver()).start(ORANGE, context=w.ctx))


def test_unified_calculator_public_analysis_finishes_before_customer_work():
    w = setup()
    s = public(w)
    assert s['status'] == 'DONE', s
    assert s['analysis_complete'] and s['stage'] == 'ORANGE'
    names = [n for n, _ in w.extra_calls]
    assert names.count('calculate_risk_score') == 1
    assert not any(n in names for n in ('get_holders', 'create_contact_task', 'mark_red', 'score_orange'))
    assert s['score_result']['expected_state_version'] == 2
    assert s['score_result']['rules_digest'] == RULES
    assert 'source_chunks' not in json.dumps(s)
    assert s['input_manifest']['structured_facts']['facts_hash'] == w.envelope['facts_hash']


def test_no_route_still_calls_calculator_for_history_without_ai_flags():
    w = setup('NONE')
    s = public(w)
    assert s['status'] == 'DONE' and s['stage'] == 'NONE'
    assert not any(p == 'extract_flags' for p, _ in w.model_calls)
    assert [a['new_signals'] for n, a in w.extra_calls if n == 'calculate_risk_score'] == [[]]


@pytest.mark.parametrize('field,value', [('rules_digest', 'changed'), ('corp_code', 'other'),
                                        ('as_of', '2099-01-01'), ('manual_check', True)])
def test_calculator_version_identity_and_manual_fail_closed(field, value):
    w = setup(); w.response[field] = value
    assert public(w)['status'] == 'MANUAL_CHECK'
    assert not w.store.tasks


def test_missing_tool_or_null_facts_never_becomes_none():
    w = setup(); w.facts.clear()
    s = public(w)
    assert s['status'] == 'MANUAL_CHECK' and 'stage' not in s
    assert not any(n == 'calculate_risk_score' for n, _ in w.extra_calls)


@pytest.mark.parametrize('status,value', [('PRESENT', 0), ('PRESENT', False), ('MISSING', None),
                                        ('NOT_APPLICABLE', None), ('NEEDS_CHECK', None)])
def test_structured_statuses_preserve_zero_false_and_missing(status, value):
    w = setup()
    f = w.facts['facts'][0]
    f.update(status=status, value=value, raw=value, dtype='boolean' if isinstance(value, bool) else 'integer',
             reason=None if status == 'PRESENT' else 'controlled-test')
    clean = validate_facts(w.facts, actual_disclosure(ORANGE)[0])
    assert clean['facts'][0]['value'] is value


def test_duplicate_rows_require_discriminator_and_version_changes_stop():
    w = setup(); w.facts['facts'].append(copy.deepcopy(w.facts['facts'][0]))
    with pytest.raises(AgentError, match='다중 행'):
        validate_facts(w.facts, actual_disclosure(ORANGE)[0])
    w = setup(); w.envelope['facts_hash'] = '0' * 64
    assert public(w)['status'] == 'MANUAL_CHECK'


def test_candidate_merge_caps_model_input_at_five():
    w = setup()
    chunks = [{**w.ev, 'text': w.ev['quote'], 'location': f'chunk-{i}'} for i in range(9)]
    assert len(select_chunks([chunks[0], *chunks])) == 5
    assert len({c['location'] for c in select_chunks([chunks[0], *chunks])}) == 5


def test_public_and_org_checkpoints_and_approval_are_isolated():
    w = setup()
    async def scenario():
        saver = InMemorySaver()
        rt = AgentRuntime(w.deps, saver)
        result = await rt.start(ORANGE, context=w.ctx)
        calls_before = len(w.extra_calls)
        review = w.ctx.model_copy(update={'kind': 'review', 'tenant_id': 'broker-A'})
        s = await rt.start_review(ORANGE, result, context=review)
        assert s['status'] == 'WAITING_APPROVAL', s
        assert not any(n == 'search_disclosure_text' for n, _ in w.extra_calls[calls_before:])
        assert (await rt.get_state(ORANGE, context=w.ctx))['status'] == 'DONE'
        assert w.ctx.thread_id(ORANGE) != review.thread_id(ORANGE)
        other = review.model_copy(update={'tenant_id': 'broker-B'})
        assert await rt.get_state(ORANGE, context=other) == {}
        pb = PBContext(pb_id='P01', tenant_id='broker-A', allowed_customer_ids=frozenset(w.owners))
        with pytest.raises(PermissionError):
            await rt.get_review(ORANGE, pb.model_copy(update={'tenant_id': 'broker-B'}), context=review)
        target = s['affected_customers'][0]['customer_id']
        batch = ApprovalBatch(decisions=[{'customer_id': target, 'status': 'approved'}])
        resumed = await rt.resume(ORANGE, batch, pb, context=review)
        assert not resumed.get('error'), resumed
        assert len(w.store.tasks) == 1
        duplicate = await rt.resume(ORANGE, batch, pb, context=review)
        assert duplicate == resumed and len(w.store.tasks) == 1
        w.integration_options['current'] = False
        with pytest.raises(PermissionError):
            await rt.resume(ORANGE, batch, pb, context=review)
        async for checkpoint in saver.alist(rt.config(ORANGE, review)):
            assert 'source_chunks' not in json.dumps(checkpoint.checkpoint, default=str)
        assert all('source_chunks' not in json.dumps(state) for state, _ in w.store.saves)
    asyncio.run(scenario())


def test_same_assessment_retry_thread_same_new_version_different():
    w = setup()
    assert w.ctx.thread_id(ORANGE) == w.ctx.model_copy(update={'attempt': 2, 'run_id': 'retry'}).thread_id(ORANGE)
    assert w.ctx.thread_id(ORANGE) != w.ctx.model_copy(update={'input_version': 'new'}).thread_id(ORANGE)


def test_unified_requires_dispatcher_context_and_injected_checkpoint():
    w = setup()
    async def scenario():
        with pytest.raises(ValueError, match='context'):
            await AgentRuntime(w.deps, InMemorySaver()).start(ORANGE)
        with pytest.raises(ValueError, match='R4'):
            async with open_runtime(w.deps):
                pass
        async with open_runtime(w.deps, checkpointer=InMemorySaver()) as rt:
            assert (await rt.start(ORANGE, context=w.ctx))['status'] == 'DONE'
    asyncio.run(scenario())


def test_provider_retries_count_against_actual_request_budget(monkeypatch):
    async def no_wait(_):
        pass
    monkeypatch.setattr('agent.harness_llm.asyncio.sleep', no_wait)
    calls = []
    class Chat:
        def with_structured_output(self, schema):
            return self
        async def ainvoke(self, messages, config):
            calls.append(1)
            error = RuntimeError('temporary'); error.status_code = 503
            raise error
    class Harness:
        recover_llm = staticmethod(recover_llm)
        async def prepare_input(self, payload):
            return payload
    model = StructuredLLM(Chat(), Chat(), Harness())
    async def scenario():
        scope = Execution(120, 2)
        token = current_execution.set(scope)
        try:
            with pytest.raises(AgentError, match='반복 한도'):
                await model.generate('select_tools', ToolSelection, {})
            assert scope.model_turns == 2 and len(calls) == 2
        finally:
            current_execution.reset(token)
    asyncio.run(scenario())


def test_api_evidence_roundtrip_not_fabricated_quote():
    from agent.structured_facts import api_evidence
    from agent.public_risk import public_risk_context
    w = setup(); f = w.facts['facts'][0]
    ev = {'kind': 'api', **{k: f['source'].get(k, f.get(k, w.facts.get(k))) for k in
          ('api', 'field', 'rcept_no', 'row_key', 'value', 'unit', 'period', 'basis', 'data_version')}}
    assert api_evidence(ev, w.facts) == ev
    validate_known_evidence([ev], [ev])
    with pytest.raises(AgentError):
        validate_known_evidence([{**ev, 'value': 'changed'}], [ev])
    p = public_risk_context({'stage': 'ORANGE', 'orange_score': 4,
                             'breakdown': [{'item': '유상증자', 'evidence': [ev]}]})
    assert p['breakdown'][0]['evidence'] == [ev] and 'orange_score' not in p


def test_transient_public_failure_retries_only_on_next_attempt():
    w = setup(); inner = w.deps.tools
    failures = {'enabled': True}
    class Tools:
        async def call(self, name, args):
            if name == 'get_disclosure_facts' and failures['enabled']:
                return ToolResponse(ok=False, error={'code': 'TIMEOUT', 'message': 'controlled timeout'})
            return await inner.call(name, args)
    w.deps = replace(w.deps, tools=Tools())
    async def scenario():
        rt = AgentRuntime(w.deps, InMemorySaver())
        first = await rt.start(ORANGE, context=w.ctx)
        assert first['status'] == 'FAILED' and first['error_retryable'], first
        assert await rt.start(ORANGE, context=w.ctx) == first
        failures['enabled'] = False
        result = await rt.start(ORANGE, context=w.ctx.model_copy(update={'attempt': 2, 'run_id': 'retry'}))
        assert result['status'] == 'DONE', result
        assert result['execution_budget']['model_turns'] > 0
    asyncio.run(scenario())


@pytest.mark.parametrize('receipt', ['20230316001076', '20210309000198', '20230313000775'])
def test_three_actual_non_risk_title_filings_skip_extraction(receipt):
    """Actual normal-title examples; NOT a claim about issuer-level risk accuracy."""
    from pathlib import Path
    from agent.rules import make_classifier
    root = Path(__file__).resolve().parents[2]
    rows = [row for p in (root / 'data/disclosures').glob('*.json') for row in json.loads(p.read_text())]
    row = next(row for row in rows if row['rcept_no'] == receipt)
    assert row['title'] == '주주총회소집공고'
    w = world()
    class Tools:
        async def call(self, name, args):
            assert name in {'get_disclosure', 'get_company_profile'}
            return ToolResponse(ok=True, data=row if name == 'get_disclosure' else
                                {'stock_code': row['stock_code'], 'name': row['corp_name']})
    class NoModel:
        async def generate(self, *args):
            pytest.fail('무관한 정상 제목에서 AI 추출을 호출하면 안 됩니다')
    deps = replace(w.deps, tools=Tools(), model=NoModel(), phase1=None, classify=make_classifier())
    result = asyncio.run(AgentRuntime(deps, InMemorySaver()).start(receipt))
    assert result['status'] == 'DONE' and not result['relevant'] and not w.store.tasks


def test_exhausted_dispatcher_ledger_stops_before_model_call():
    w = setup()
    s = asyncio.run(AgentRuntime(w.deps, InMemorySaver()).start(
        ORANGE, context=w.ctx.model_copy(update={'model_requests_used': 8})))
    assert s['status'] == 'MANUAL_CHECK'
    assert not w.model_calls


def test_new_graph_api_evidence_survives_explanation_without_fake_quote():
    w = setup()
    from agent.structured_facts import api_evidence
    fact = w.facts['facts'][0]
    ev = {'kind': 'api', **{k: fact['source'].get(k, fact.get(k, w.facts.get(k))) for k in
          ('api', 'field', 'rcept_no', 'row_key', 'value', 'unit', 'period', 'basis', 'data_version')}}
    w.response['breakdown'] = [{'item': '유상증자', 'evidence': [ev]}]
    s = public(w)
    assert s['status'] == 'DONE', s
    assert 'piicDecsn.ic_mthn' in s['ai_explanation']
    assert 'quote' not in s['score_result']['breakdown'][0]['evidence'][0]


def test_future_historical_evidence_is_rejected_before_search():
    w = setup(); ev = {**w.ev, 'rcept_no': '20250321802001'}
    w.response['breakdown'] = [{'item': '감사의견', 'evidence': [ev]}]
    inner = w.deps.tools
    class Tools:
        async def call(self, name, args):
            if name == 'get_disclosure' and args['rcept_no'] == ev['rcept_no']:
                return ToolResponse(ok=True, data={'corp_code': w.response['corp_code'], 'filed_at': '2025-03-21'})
            return await inner.call(name, args)
    w.deps = replace(w.deps, tools=Tools())
    assert public(w)['status'] == 'MANUAL_CHECK'
    assert not any(n == 'search_disclosure_text' and a['rcept_no'] == ev['rcept_no'] for n, a in w.extra_calls)


def test_real_r4_calculator_schema_when_integration_tree_available():
    import importlib.util
    if importlib.util.find_spec('risk_engine.stages') is None:
        pytest.skip('R4 stages 모듈은 임시 통합 트리에서 검증')
    from risk_engine.stages import calculate
    result = calculate(new_signals=[], history=[], as_of='2023-06-20')
    result.update(expected_state_version=0, corp_code=actual_disclosure(ORANGE)[0]['corp_code'])
    parsed = UnifiedResult.model_validate(result)
    assert parsed.stage == 'NONE' and not parsed.manual_check
