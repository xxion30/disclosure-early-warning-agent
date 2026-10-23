"""R3 collection -> real R4 storage/tool -> R2 receive (offline transport).

No real model/DART calls or financial accuracy claims. Other owners' helpers
are reused read-only; their fixtures use an isolated SQLite database.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.execution import Execution, current_execution
from agent.nodes.receive import receive
from agent.ports import AgentError
from agent.structured_facts import canonical_facts_hash, validate_facts
from mcp_servers.business.tools.get_company_profile import get_company_profile
from mcp_servers.business.tools.get_disclosure import get_disclosure
from mcp_servers.business.tools.get_disclosure_facts import get_disclosure_facts
from shared.facts import facts_hash
from shared.schemas import ToolResponse
from tests.collector.conftest import db, fake  # noqa: F401 -- pytest fixtures
from tests.collector.test_jobs_facts import TARGET, TODAY, setup_chain
from collector.jobs import collect_stock

RECEIPT = '20241031000530'  # Actual KY rights-issue filing used by R3's fixture.


@pytest.fixture()
def tool_data(db, fake):
    setup_chain(fake)
    outcome = asyncio.run(collect_stock(fake.client(), TARGET, today=TODAY))
    assert outcome.ok
    response = get_disclosure_facts(RECEIPT)
    assert response['ok'] and response['data']['availability'] == 'AVAILABLE'
    return response['data']


def identity(data):
    return {key: data[key] for key in ('rcept_no', 'corp_code')}


def rehash(data):
    data['facts_hash'] = facts_hash(data['structured_facts'])
    return data


def test_actual_collector_response_is_accepted_without_an_external_shape_adapter(tool_data):
    before = deepcopy(tool_data)
    parsed = validate_facts(tool_data, identity(tool_data))
    assert parsed['facts_hash'] == tool_data['facts_hash']
    assert canonical_facts_hash(tool_data['structured_facts']) == tool_data['facts_hash']
    assert {f['dtype'] for f in parsed['facts']} >= {'number', 'integer', 'boolean'}
    assert any(f['name'] == 'issue_ratio_common_pct' for f in parsed['facts'])
    assert tool_data == before
    assert 'content_hash' not in parsed and 'data_version' not in parsed


def run_receive(data, version, *, envelope=True):
    calls = []
    class Tools:
        async def call(self, name, args):
            calls.append(name)
            if name == 'get_disclosure_facts':
                return ToolResponse(ok=True, data=deepcopy(data if envelope else data['structured_facts']))
            function = {'get_disclosure': get_disclosure, 'get_company_profile': get_company_profile}[name]
            return ToolResponse.model_validate(function(**args))
    class Harness:
        async def recover_tool(self, operation):
            return await operation()
    deps = SimpleNamespace(tools=Tools(), harness=Harness(), phase1=None, unified=object())
    context = {'as_of': '2025-04-01', 'input_version': 'entire-manifest-not-the-facts-hash'}
    if version is not None:
        context['facts_version'] = version
    state = {'disclosure': {'rcept_no': RECEIPT}, 'execution_context': context}
    async def execute():
        token = current_execution.set(Execution(120, 8))
        try:
            return await receive(state, deps)
        finally:
            current_execution.reset(token)
    return asyncio.run(execute()), calls


def test_actual_stored_facts_reach_receive_with_separate_manifest_and_facts_versions(tool_data):
    output, calls = run_receive(tool_data, tool_data['facts_hash'])
    assert output['structured_facts']['facts_hash'] == tool_data['facts_hash']
    assert output['disclosure']['rcept_no'] == RECEIPT
    assert 'body_text' not in output['disclosure']
    assert calls == ['get_disclosure', 'get_company_profile', 'get_disclosure_facts']


@pytest.mark.parametrize('version', [None, '0' * 64])
def test_missing_or_stale_facts_version_cannot_fall_back_to_input_version(tool_data, version):
    with pytest.raises(AgentError, match='facts_version|구조화 자료 버전'):
        run_receive(tool_data, version)


def test_receive_does_not_accept_an_unwrapped_fixture_as_a_real_mcp_response(tool_data):
    with pytest.raises(AgentError, match='중첩 응답'):
        run_receive(tool_data, tool_data['facts_hash'], envelope=False)


@pytest.mark.parametrize('where', ['envelope', 'payload'])
@pytest.mark.parametrize('key', ['corp_code', 'rcept_no'])
def test_response_and_payload_must_belong_to_the_requested_disclosure(tool_data, where, key):
    data = deepcopy(tool_data)
    target = data if where == 'envelope' else data['structured_facts']
    target[key] = '20240927000916' if key == 'rcept_no' else 'another-corp'
    rehash(data)
    with pytest.raises(AgentError, match='공시·기업'):
        validate_facts(data, identity(tool_data))


@pytest.mark.parametrize('bad', [True, '10.2', None, float('nan'), float('inf')])
def test_number_is_a_finite_json_number_not_bool_or_a_string(tool_data, bad):
    data = deepcopy(tool_data)
    fact = next(f for f in data['structured_facts']['facts'] if f['dtype'] == 'number')
    fact.update(status='PRESENT', value=bad)
    if isinstance(bad, float) and not __import__('math').isfinite(bad):
        with pytest.raises(AgentError, match='유한값'):
            validate_facts(data, identity(data))
    else:
        rehash(data)
        with pytest.raises(AgentError, match='수치|PRESENT'):
            validate_facts(data, identity(data))


def test_false_and_zero_are_retained_and_missing_is_still_null(tool_data):
    data = deepcopy(tool_data)
    number = next(f for f in data['structured_facts']['facts'] if f['dtype'] == 'number')
    number.update(status='PRESENT', value=0)
    boolean = next(f for f in data['structured_facts']['facts'] if f['dtype'] == 'boolean')
    boolean.update(status='PRESENT', value=False)
    other = next(f for f in data['structured_facts']['facts'] if f is not number and f is not boolean)
    other.update(status='MISSING', value=None, reason='검증용 결측')
    parsed = validate_facts(rehash(data), identity(data))
    by_name = {f['name']: f for f in parsed['facts']}
    assert by_name[number['name']]['value'] == 0
    assert by_name[boolean['name']]['value'] is False
    assert by_name[other['name']]['value'] is None


def test_payload_changes_without_rehash_are_rejected(tool_data):
    data = deepcopy(tool_data)
    data['structured_facts']['facts'].pop()
    with pytest.raises(AgentError, match='facts_hash 불일치'):
        validate_facts(data, identity(data))


@pytest.mark.parametrize('digest', [None, '', 'changed', '0' * 64])
def test_missing_malformed_or_wrong_hash_is_rejected(tool_data, digest):
    data = deepcopy(tool_data); data['facts_hash'] = digest
    with pytest.raises(AgentError, match='facts_hash'):
        validate_facts(data, identity(data))


def test_unavailable_is_not_an_empty_success_or_zero_risk(tool_data):
    data = {**identity(tool_data), 'availability': 'UNAVAILABLE',
            'structured_facts': None, 'facts_hash': None}
    with pytest.raises(AgentError, match='이용 불가'):
        validate_facts(data, identity(data))


def test_period_and_basis_distinguish_rows_but_exact_duplicates_are_rejected(tool_data):
    data = deepcopy(tool_data)
    first = deepcopy(data['structured_facts']['facts'][0])
    first['source'].update(period='2024', basis='OFS')
    second = deepcopy(first); second['source']['basis'] = 'CFS'
    third = deepcopy(first); third['source']['period'] = '2023'
    data['structured_facts']['facts'] = [first, second, third]
    assert len(validate_facts(rehash(data), identity(data))['facts']) == 3
    data['structured_facts']['facts'].append(deepcopy(first))
    with pytest.raises(AgentError, match='다중 행'):
        validate_facts(rehash(data), identity(data))


def test_mcp_v1_rejects_legacy_decimal_and_unknown_schema(tool_data):
    for edit in ('dtype', 'schema_version'):
        data = deepcopy(tool_data)
        if edit == 'dtype':
            next(f for f in data['structured_facts']['facts'] if f['dtype'] == 'number')['dtype'] = 'decimal'
        else:
            data['structured_facts']['schema_version'] = 'unknown'
        with pytest.raises(AgentError, match='자료형|스키마'):
            validate_facts(rehash(data), identity(data))
