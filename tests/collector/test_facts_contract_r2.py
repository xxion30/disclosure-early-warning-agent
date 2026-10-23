"""R3가 저장한 사실이 R4 도구(get_disclosure_facts)를 거쳐 R2 검증기(agent.structured_facts.validate_facts)에 닿는지 본다.

R3 payload → R4 store_facts → R4 get_disclosure_facts → R2 validate_facts. 아래 셋은 2026-10-23 통합 브랜치(386d676)에서 실패한다.
고쳐지면 strict xfail이 XPASS로 실패해 알려 주므로, 그때 이 표시(xfail)를 지운다. 고치는 곳은 R2(agent/structured_facts.py, receive.py)와
R4(shared/facts.py, 도구 응답)이며 R3 코드는 바꾸지 않는다.
"""
import copy

import pytest

from agent.ports import AgentError
from agent.structured_facts import validate_facts
from collector.jobs import collect_stock
from mcp_servers.business.tools.get_disclosure_facts import get_disclosure_facts
from tests.collector.test_jobs_facts import TARGET, TODAY, run, setup_chain

RCEPT = "20241031000530"  # 금양 유상증자결정(실제 접수번호). 값은 가짜 DART 응답이지만 필드 모양은 항목 API 그대로다


@pytest.fixture()
def tool_data(db, fake):
    """R2가 invoke_tool로 받는 값 그대로 (ToolResponse.data)."""
    setup_chain(fake)
    run(collect_stock(fake.client(), TARGET, today=TODAY))
    data = get_disclosure_facts(RCEPT)["data"]
    assert data["availability"] == "AVAILABLE"
    return data


def test_the_stored_facts_include_ratios_so_the_dtype_gap_is_real(tool_data):
    dtypes = {f["dtype"] for f in tool_data["structured_facts"]["facts"]}
    assert "number" in dtypes  # 신주 비율 등 R4 규칙이 쓰는 값은 R4 검증기에서 number여야 저장된다 (decimal은 저장 거절)


@pytest.mark.xfail(strict=True, raises=AgentError, reason="R2는 최상위 facts를 기대하지만 R4 도구는 structured_facts 아래에 준다")
def test_r2_validator_accepts_the_tool_response_shape(tool_data):
    data = copy.deepcopy(tool_data)
    data["structured_facts"]["facts"] = [f for f in data["structured_facts"]["facts"] if f["dtype"] != "number"]  # 자료형 문제를 빼고 모양만 본다
    validate_facts(data, {"rcept_no": RCEPT, "corp_code": data["corp_code"]})


@pytest.mark.xfail(strict=True, raises=AgentError, reason="R2는 string·integer·decimal·boolean·date만 받고 R4는 number를 쓴다")
def test_r2_validator_accepts_the_number_dtype(tool_data):
    unwrapped = {**tool_data["structured_facts"], "content_hash": tool_data["facts_hash"]}  # 모양 문제는 사람이 풀어 준다
    validate_facts(unwrapped, {"rcept_no": RCEPT, "corp_code": tool_data["corp_code"]})


@pytest.mark.xfail(strict=True, raises=(AgentError, AssertionError), reason="모양·자료형·버전 키(facts_hash↔content_hash/data_version)가 모두 맞아야 한다")
def test_tool_response_reaches_r2_with_a_version_it_can_compare(tool_data):
    facts = validate_facts(tool_data, {"rcept_no": RCEPT, "corp_code": tool_data["corp_code"]})
    assert facts.get("content_hash") or facts.get("data_version")  # receive.py가 input_version과 비교하는 값
