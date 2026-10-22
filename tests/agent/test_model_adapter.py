"""모델 연결·인용 보정·입력 준비·분류 테스트 (담당: R2).

agent/model_adapter.py, agent/harness_llm.py, agent/rules.py를 서버·DB 없이 확인한다.
(조립 전체 흐름은 tests/wiring/test_assembly.py, 담당 R4)
"""
import asyncio

import pytest

from agent.adapters.harness_tools import recover_tool
from agent.harness_llm import prepare_input, recover_llm
from agent.rules import signal_input
from harness import recovery_llm as recovery
from shared.schemas import ToolResponse

RCEPT = "20250321802001"  # 금양 감사보고서제출


def _settings(**kw):
    from types import SimpleNamespace

    base = dict(OPENAI_API_KEY="sk-test", ANTHROPIC_API_KEY="", LLM_PRIMARY="claude-haiku-5-5",
                LLM_FALLBACK="gpt-5.4-mini", FAKE_LLM_ERROR="")
    return SimpleNamespace(**{**base, **kw})


def _name(m):
    return getattr(m, "model_name", getattr(m, "model", None))


def test_prepare_input_masks_pii_but_keeps_dates():
    out = asyncio.run(prepare_input({
        "disclosure": {"filed_at": "2025-03-21", "rcept_no": RCEPT},
        "customers": [{"customer_id": "C001", "name": "한지우", "phone": "010-1234-5678"}],
        "note": "계좌 110-123-456789",
    }))
    assert out["disclosure"]["filed_at"] == "2025-03-21"
    assert out["customers"][0] == {"customer_id": "C001", "name": "고객 C001", "phone": "***"}
    assert out["note"] == "계좌 ***"


def test_prepare_input_fits_token_budget():
    from harness import context

    big = {"chunks": [{"text": "가" * 800} for _ in range(30)]}
    out = asyncio.run(prepare_input(big))
    assert context.estimate_tokens(str(out)) <= context.TOKEN_BUDGET + 200


def test_recover_llm_retries_then_falls_back(monkeypatch):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr("agent.harness_llm.audit_system", noop)

    class E503(Exception):
        status_code = 503

    calls = {"primary": 0}

    async def primary():
        calls["primary"] += 1
        raise E503()

    async def fallback():
        return "fallback-ok"

    assert asyncio.run(recover_llm(primary, fallback)) == "fallback-ok"
    assert calls["primary"] == 3 and sleeps == [1, 3]  # 최초 1회 + 재시도 2회(1초, 3초)

    async def fallback_fails():
        raise E503()

    with pytest.raises(recovery.LLMUnavailable):
        asyncio.run(recover_llm(primary, fallback_fails))


def test_recover_llm_does_not_retry_validation_errors():
    calls = {"n": 0}

    async def primary():
        calls["n"] += 1
        raise ValueError("schema")

    with pytest.raises(ValueError):
        asyncio.run(recover_llm(primary, primary))
    assert calls["n"] == 1


def test_recover_tool_retries_timeout_once(monkeypatch):
    async def noop(*a, **k):
        return None

    monkeypatch.setattr("agent.adapters.harness_tools.audit_system", noop)
    results = [ToolResponse(ok=False, error={"code": "TIMEOUT", "message": "t"}), ToolResponse(ok=True, data={"x": 1})]

    async def op():
        return results.pop(0)

    assert asyncio.run(recover_tool(op)).ok is True and results == []


def test_signal_input_never_carries_points():
    out = signal_input({"facts": [{"signal_type": "FILING_DELAY", "values": {}, "evidence": [
        {"source": "s", "quote": "q", "rcept_no": "1", "location": "l"}]}]})
    assert out == [{"signal_type": "FILING_DELAY", "detail": {}, "evidence": [
        {"source": "s", "quote": "q", "rcept_no": "1", "location": "l"}], "rcept_no": "1"}]
    assert "points" not in out[0]


def test_model_openai_only_when_no_anthropic_key():
    from agent.model_adapter import build_model

    m = build_model(None, _settings())
    assert _name(m.primary) == "gpt-5.4-mini" and _name(m.fallback) == "gpt-5.4-mini"


def test_model_openai_primary_by_name():
    from agent.model_adapter import build_model

    m = build_model(None, _settings(LLM_PRIMARY="gpt-5.4", ANTHROPIC_API_KEY="x"))
    assert _name(m.primary) == "gpt-5.4" and _name(m.fallback) == "gpt-5.4-mini"


def test_model_anthropic_primary_when_key_present():
    from agent.model_adapter import build_model

    m = build_model(None, _settings(ANTHROPIC_API_KEY="sk-ant-test"))
    assert _name(m.primary) == "claude-haiku-5-5" and _name(m.fallback) == "gpt-5.4-mini"


def test_model_requires_openai_key():
    from agent.model_adapter import build_model

    with pytest.raises(ValueError):
        build_model(None, _settings(OPENAI_API_KEY=""))


def test_openai_models_use_function_calling_for_structured_output():
    """strict json_schema는 dict[str, Any]를 거부하므로 function calling을 기본으로 쓴다."""
    from agent.model_adapter import _openai_chat
    from agent.state import ExplanationOutput, FactsOutput

    m = _openai_chat("gpt-5.4-mini", "sk-test")
    for schema in (FactsOutput, ExplanationOutput):
        bound = m.with_structured_output(schema)
        assert "tool" in str(bound.first.kwargs).lower() or "function" in str(bound.first.kwargs).lower()


def test_classification_skips_periodic_reports_and_keeps_ownership():
    from agent.rules import load_classifier
    from risk_engine.loader import DEFAULT_PATH

    classify = load_classifier(DEFAULT_PATH)
    assert classify("분기보고서 (2024.09)") is None and classify("[기재정정]사업보고서 (2023.12)") is None
    assert classify("주식등의대량보유상황보고서(일반)") == "OWNERSHIP"


def test_explain_risk_citations_are_replaced_by_verified_evidence():
    """AI가 인용을 바꿔 써도(말줄임, 요약) 점수 내역의 검증된 근거로 대체된다."""
    from agent.model_adapter import KnownSignalsModel
    from agent.state import ExplanationOutput

    ev = {"source": "s", "quote": "원문 그대로의 긴 인용문", "rcept_no": "1", "location": "chunk-0"}
    bad = {"source": "s", "quote": "원문 그대로의…(줄임)", "rcept_no": "1", "location": "chunk-0"}

    class Inner:
        async def generate(self, prompt, schema, payload):
            return schema.model_validate({"items": [{"breakdown_index": 0, "explanation": "설명", "evidence": [bad]}]})

    out = asyncio.run(KnownSignalsModel(Inner()).generate(
        "explain_risk", ExplanationOutput, {"score_result": {"breakdown": [{"evidence": [ev]}]}}))
    assert out.items[0].evidence[0].quote == ev["quote"] and out.items[0].explanation == "설명"


def test_explain_risk_gets_exactly_one_item_per_breakdown():
    """AI가 항목을 빠뜨리거나 중복해도 점수 내역 개수만큼 하나씩 채워진다."""
    from agent.model_adapter import KnownSignalsModel
    from agent.state import ExplanationOutput

    ev = lambda n: {"source": "s", "quote": f"인용{n}", "rcept_no": "1", "location": f"chunk-{n}"}  # noqa: E731
    breakdown = [{"item": f"항목{n}", "points": 1.0, "weight": 0.5, "evidence": [ev(n)]} for n in range(4)]

    class Inner:
        async def generate(self, prompt, schema, payload):
            mk = lambda i, t: {"breakdown_index": i, "explanation": t, "evidence": [ev(i)]}  # noqa: E731
            return schema.model_validate({"items": [mk(0, "A"), mk(0, "중복"), mk(2, "C")]})

    out = asyncio.run(KnownSignalsModel(Inner()).generate(
        "explain_risk", ExplanationOutput, {"score_result": {"breakdown": breakdown}}))
    assert [i.breakdown_index for i in out.items] == [0, 1, 2, 3]
    assert out.items[0].explanation == "A" and out.items[2].explanation == "C"
    assert out.items[1].explanation.startswith("[규칙 요약]") and out.items[1].evidence[0].quote == "인용1"


def test_langsmith_env_is_exported_only_outside_pytest(monkeypatch):
    from types import SimpleNamespace

    from harness.tracing import enable_langsmith

    s = SimpleNamespace(LANGSMITH_TRACING=True, LANGSMITH_API_KEY="ls-test", LANGSMITH_PROJECT="p")
    for k in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT"):
        monkeypatch.setenv(k, "x")  # 테스트가 끝나면 원래(없음)로 되돌리도록 먼저 기록해 둔다
        monkeypatch.delenv(k)
    assert enable_langsmith(s) is False  # pytest 안에서는 보내지 않는다
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert enable_langsmith(s) is True
    import os

    assert os.environ["LANGSMITH_TRACING"] == "true" and os.environ["LANGSMITH_PROJECT"] == "p"
    assert enable_langsmith(SimpleNamespace(LANGSMITH_TRACING=True, LANGSMITH_API_KEY="", LANGSMITH_PROJECT="p")) is False


def test_snap_quote_keeps_exact_and_repairs_stitched_quotes():
    from agent.model_adapter import snap_quote

    text = "1. 신주의 종류와 수 보통주식 (주) 74,700,000 기타주식 (주) - 2. 1주당 액면가액 (원) 5,000 3. 증자전 발행주식총수 (주) 47,728,537"
    assert snap_quote("1주당 액면가액 (원) 5,000", text) == "1주당 액면가액 (원) 5,000"  # 이미 원문 그대로
    stitched = "신주의 종류와 수 보통주식 (주) 74,700,000 기타주식 (주) - 3. 증자전 발행주식총수 (주) 47,728,537"  # 가운데를 건너뛰고 이어 붙임
    snapped = snap_quote(stitched, text)
    assert snapped and snapped in text and "74,700,000" in snapped  # 원문 그대로의 연속 구간으로 보정됨
    assert snap_quote("전혀 관련 없는 문장입니다 정말로 아무 상관이 없습니다", text) is None


def test_extract_facts_evidence_is_snapped_to_verbatim_text():
    from agent.model_adapter import KnownSignalsModel
    from agent.state import FactsOutput

    text = "신주의 종류와 수 보통주식 (주) 74,700,000 기타주식 (주) - 2. 1주당 액면가액 (원) 5,000 3. 증자전 발행주식총수 (주) 47,728,537"
    chunk = {"source": "s", "rcept_no": "1", "location": "chunk-0", "text": text}
    bad = {"source": "s", "rcept_no": "1", "location": "chunk-0",
           "quote": "신주의 종류와 수 보통주식 (주) 74,700,000 기타주식 (주) - 3. 증자전 발행주식총수 (주) 47,728,537"}

    class Inner:
        async def generate(self, prompt, schema, payload):
            return schema.model_validate({"facts": [{"signal_type": "RIGHTS_OFFERING", "values": {"ratio_pct": 156.4}, "evidence": [bad]}]})

    out = asyncio.run(KnownSignalsModel(Inner()).generate("extract_facts", FactsOutput, {"chunks": [chunk]}))
    quote = out.facts[0].evidence[0].quote
    assert quote in text and "74,700,000" in quote  # A의 validate_evidence(quote in text)를 통과하는 형태


def test_attachment_only_corrections_are_not_analyzed():
    from agent.rules import make_classifier

    classify = make_classifier()
    assert classify("[첨부정정]감사보고서제출") is None and classify("[첨부추가]감사보고서제출") is None
    assert classify("감사보고서제출") == "AUDIT_REPORT" and classify("[기재정정]감사보고서제출") == "AUDIT_REPORT"


def test_evidence_metadata_is_repaired_from_the_search_chunk():
    """AI가 접수번호·출처·위치를 잘못 베껴도(마스킹된 값 등) 검색 조각의 값으로 바로잡힌다."""
    from agent.model_adapter import KnownSignalsModel
    from agent.state import FactsOutput

    text = "횡령 등 금액 발생금액(원) 188,000,000,000 자기자본(원) 204,760,579,444 자기자본대비(%) 91.81"
    chunk = {"source": "오스템 횡령ㆍ배임혐의발생 (2022-01-03)", "rcept_no": "20220103900001", "location": "chunk-0 (0-602자)", "text": text}
    bad = {"source": "오스템 횡령ㆍ배임혐의발생 (2022-01-03)", "rcept_no": "2022***", "location": "chunk-0 (0-602자)", "quote": text}

    class Inner:
        async def generate(self, prompt, schema, payload):
            return schema.model_validate({"facts": [{"signal_type": "EMBEZZLEMENT", "values": {"ratio_to_equity_pct": 91.81}, "evidence": [bad]}]})

    out = asyncio.run(KnownSignalsModel(Inner()).generate("extract_facts", FactsOutput, {"chunks": [chunk]}))
    e = out.facts[0].evidence[0]
    assert (e.rcept_no, e.source, e.location) == (chunk["rcept_no"], chunk["source"], chunk["location"])
    from agent.evidence import validate_evidence

    validate_evidence([e.model_dump()], [chunk])  # A의 검증을 통과한다


def test_explain_risk_passes_through_when_a_breakdown_item_has_no_evidence():
    """근거가 없는 점수 항목이 있으면 지어내지 않고 그대로 넘겨 A의 검증이 수동 확인으로 처리하게 한다."""
    from agent.model_adapter import KnownSignalsModel
    from agent.state import ExplanationOutput

    ev = {"source": "s", "quote": "q", "rcept_no": "1", "location": "l"}
    ai = {"items": [{"breakdown_index": 0, "explanation": "설명", "evidence": [ev]}, {"breakdown_index": 1, "explanation": "설명", "evidence": [ev]}]}

    class Inner:
        async def generate(self, prompt, schema, payload):
            return schema.model_validate(ai)

    bd = [{"item": "a", "evidence": [ev]}, {"item": "근거 없는 항목", "evidence": []}]
    out = asyncio.run(KnownSignalsModel(Inner()).generate("explain_risk", ExplanationOutput, {"score_result": {"breakdown": bd}}))
    assert len(out.items) == 2 and out.items[1].explanation == "설명"  # 보정하지 않고 AI 결과 그대로


def test_signal_types_must_fit_the_disclosure_type():
    """대량보유보고서(OWNERSHIP)에서 AI가 CB_BW 신호를 올려도 걸러진다. 주식 담보(PLEDGE)만 허용."""
    from agent.model_adapter import KnownSignalsModel
    from agent.state import FactsOutput

    text = "전환사채권 - 10,479,375 보유 현황"
    chunk = {"source": "s", "text": text, "rcept_no": "1", "location": "chunk-0"}
    ev = {"source": "s", "quote": text, "rcept_no": "1", "location": "chunk-0"}

    class Inner:
        async def generate(self, prompt, schema, payload):
            return schema.model_validate({"facts": [
                {"signal_type": "CB_BW", "values": {"refixing": True}, "evidence": [ev]},
                {"signal_type": "PLEDGE", "values": {}, "evidence": [ev]},
            ]})

    model = KnownSignalsModel(Inner())
    out = asyncio.run(model.generate("extract_facts", FactsOutput, {"chunks": [chunk], "disclosure_type": "OWNERSHIP"}))
    assert [f.signal_type for f in out.facts] == ["PLEDGE"]
    out = asyncio.run(model.generate("extract_facts", FactsOutput, {"chunks": [chunk], "disclosure_type": "CB_BW"}))
    assert [f.signal_type for f in out.facts] == ["CB_BW"]
    out = asyncio.run(model.generate("extract_facts", FactsOutput, {"chunks": [chunk]}))  # 유형을 모르면 기존 동작
    assert len(out.facts) == 2
