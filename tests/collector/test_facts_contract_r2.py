"""R3가 저장한 사실이 R4 도구(get_disclosure_facts)를 거쳐 R2 검증기(agent.structured_facts.validate_facts)에 닿는지 본다.

R3 payload → R4 store_facts → R4 get_disclosure_facts → R2 validate_facts.
R4 응답 투영으로 연결을 수정했다. 저장용 number는 유지하고 조회의 최상위 facts에는 decimal을 제공한다.
이 시험은 저장용 호환 필드를 수동으로 풀지 않고 실제 R2 입력인 ToolResponse.data를 검증한다.
"""
import copy

import pytest

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


def test_r2_validator_accepts_the_tool_response_shape(tool_data):
    data = copy.deepcopy(tool_data)
    data["facts"] = [f for f in data["facts"] if f["dtype"] != "decimal"]  # 숫자 변환은 다음 시험에서 검증한다
    validate_facts(data, {"rcept_no": RCEPT, "corp_code": data["corp_code"]})


def test_r2_validator_accepts_the_number_dtype(tool_data):
    facts = validate_facts(tool_data, {"rcept_no": RCEPT, "corp_code": tool_data["corp_code"]})
    numbers = [f for f in tool_data["structured_facts"]["facts"] if f["dtype"] == "number"]
    for stored in numbers:
        converted = next(f for f in facts["facts"] if f["name"] == stored["name"] and f["source"] == stored["source"])
        assert converted == {**stored, "dtype": "decimal"}


def test_tool_response_reaches_r2_with_a_version_it_can_compare(tool_data):
    facts = validate_facts(tool_data, {"rcept_no": RCEPT, "corp_code": tool_data["corp_code"]})
    assert facts["data_version"] == tool_data["facts_hash"]
