"""Real R4 tool -> MCP unwrap -> R2 validator/receive; no model or network.

Values below exercise the protocol only, not actual issuer financial facts.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

import shared.db as db_module
from agent.adapters.mcp_client import MCPTools
from agent.execution import Execution, current_execution, invoke_tool
from agent.nodes.receive import receive
from agent.ports import AgentError
from agent.structured_facts import api_evidence, validate_facts
from api.worker.analysis import AnalysisWorker
from mcp_servers.business.tools.get_company_profile import get_company_profile
from mcp_servers.business.tools.get_disclosure import get_disclosure
from mcp_servers.business.tools.get_disclosure_facts import get_disclosure_facts
from shared.analysis import enqueue, mark_ready
from shared.facts import store_facts
from shared.models import AnalysisJob, Disclosure

RCEPT = '20250321802001'
DAY = '2025-03-21'


def payload():
    with db_module.get_session() as db:
        corp = db.get(Disclosure, RCEPT).corp_code
    return {'schema_version': '1', 'rcept_no': RCEPT, 'corp_code': corp,
            'collected_at': '2025-03-21T09:05:00+09:00',
            'facts': [{'name': 'contract_value', 'dtype': 'number', 'raw': '12.5',
                       'value': 12.5, 'unit': '%', 'status': 'PRESENT', 'reason': None,
                       'source': {'api': 'contract_fixture', 'field': 'value', 'rcept_no': RCEPT,
                                  'fetched_at': '2025-03-21T09:05:00+09:00'}}]}


class LocalTool:
    def __init__(self, function):
        self.function = function

    async def ainvoke(self, request):
        return self.function(**request['args'])


class Harness:
    async def recover_tool(self, operation):
        return await operation()


def dependencies():
    tools = MCPTools({name: LocalTool(function) for name, function in {
        'get_disclosure_facts': get_disclosure_facts,
        'get_disclosure': get_disclosure,
        'get_company_profile': get_company_profile,
    }.items()})
    return SimpleNamespace(tools=tools, harness=Harness(), phase1=None, unified=object())


async def scoped(operation):
    token = current_execution.set(Execution(60, 8))
    try:
        return await operation()
    finally:
        current_execution.reset(token)


def prepare(value):
    with db_module.get_session() as db:
        version = store_facts(db, RCEPT, value)
        mark_ready(db, RCEPT)
        job = enqueue(db, RCEPT, DAY, 'contract-rules-version')
        job_id, input_hash = job.job_id, job.input_hash
    return version, job_id, input_hash


@pytest.mark.parametrize(('dtype', 'value', 'status', 'reason'), [
    ('number', 12.5, 'PRESENT', None),
    ('number', 0, 'PRESENT', None),
    ('integer', 0, 'PRESENT', None),
    ('boolean', False, 'PRESENT', None),
    ('string', '2025-03-21', 'PRESENT', None),
    ('number', None, 'MISSING', 'source value missing'),
    ('number', None, 'NOT_APPLICABLE', 'not applicable'),
    ('number', None, 'NEEDS_CHECK', 'requires review'),
])
def test_actual_tool_response_passes_r2_without_storage_changes(seeded_db, dtype, value, status, reason):
    original = payload()
    original['facts'][0].update(dtype=dtype, value=value, status=status, reason=reason)
    with db_module.get_session() as db:
        version = store_facts(db, RCEPT, original)
        stored = deepcopy(db.get(Disclosure, RCEPT).structured_facts)
    deps = dependencies()
    raw = asyncio.run(scoped(lambda: invoke_tool(deps.tools, deps.harness, 'get_disclosure_facts', {'rcept_no': RCEPT})))
    facts = validate_facts(raw, original)
    item = facts['facts'][0]
    assert item['dtype'] == ('decimal' if dtype == 'number' else dtype)
    assert item['value'] == value and type(item['value']) is type(value)
    assert item['raw'] == stored['facts'][0]['raw']
    assert item['source'] == stored['facts'][0]['source']
    assert item['status'] == status and item['unit'] == '%'
    assert facts['data_version'] == raw['facts_hash'] == version
    assert raw['structured_facts'] == stored  # temporary R3 compatibility
    with db_module.get_session() as db:
        assert db.get(Disclosure, RCEPT).structured_facts == stored
    if status == 'PRESENT':
        source = item['source']
        evidence = {'kind': 'api', 'api': source['api'], 'field': source['field'],
                    'rcept_no': RCEPT, 'row_key': None, 'value': value, 'unit': '%',
                    'period': None, 'basis': None, 'data_version': version}
        assert api_evidence(evidence, facts) == evidence


def test_null_is_not_a_successful_empty_list(seeded_db):
    raw = get_disclosure_facts(RCEPT)['data']
    assert raw['availability'] == 'UNAVAILABLE'
    assert raw['facts'] is raw['data_version'] is None
    with pytest.raises(AgentError, match='미수집'):
        validate_facts(raw, {'rcept_no': RCEPT, 'corp_code': raw['corp_code']})
    value = payload()
    value['facts'] = []
    with db_module.get_session() as db:
        version = store_facts(db, RCEPT, value)
    raw = get_disclosure_facts(RCEPT)['data']
    assert raw['availability'] == 'AVAILABLE'
    assert validate_facts(raw, value)['facts'] == []
    assert raw['data_version'] == version


@pytest.mark.parametrize('changed', [False, True])
def test_worker_context_uses_pinned_facts_and_real_r2_receive(seeded_db, changed):
    original = payload()
    version, job_id, input_hash = prepare(original)
    assert version != input_hash  # full manifest is a different version domain
    if changed:
        updated = deepcopy(original)
        updated['facts'][0]['value'] = 25
        with db_module.get_session() as db:
            assert store_facts(db, RCEPT, updated) != version
    contexts = []

    async def execute(context):
        contexts.append(context)
        public_context = context.execution_context
        assert public_context['facts_version'] == version
        assert public_context['input_version'] == input_hash
        assert public_context['run_id'] == public_context['assessment_key'] == job_id
        assert public_context['kind'] == 'public' and public_context['tenant_id'] is None
        operation = lambda: receive({'disclosure': {'rcept_no': RCEPT},
                                     'execution_context': public_context}, dependencies())
        if changed:
            with pytest.raises(AgentError, match='버전 불일치'):
                await scoped(operation)
        else:
            result = await scoped(operation)
            assert result['structured_facts']['data_version'] == version
        # This test stops at the facts boundary; no fabricated assessment is published.
        return {'status': 'MANUAL_CHECK'}

    asyncio.run(AnalysisWorker(execute).run_once())
    assert len(contexts) == 1
    # The worker catches executor exceptions: assert persisted status so an
    # assertion inside execute cannot accidentally produce a passing test.
    with db_module.get_session() as db:
        assert db.get(AnalysisJob, job_id).status == 'MANUAL_CHECK'


def test_worker_preserves_unavailable_facts_version(seeded_db):
    with db_module.get_session() as db:
        mark_ready(db, RCEPT)
        job_id = enqueue(db, RCEPT, DAY, 'contract-rules-version').job_id
    contexts = []

    async def execute(context):
        contexts.append(context.execution_context)
        return {'status': 'MANUAL_CHECK'}

    asyncio.run(AnalysisWorker(execute).run_once())
    assert len(contexts) == 1
    assert contexts[0]['facts_version'] is None
    assert contexts[0]['input_version']
    with db_module.get_session() as db:
        assert db.get(AnalysisJob, job_id).status == 'MANUAL_CHECK'
