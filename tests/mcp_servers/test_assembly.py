"""agent.assembly 조립 테스트.

진짜 B 도구 함수·진짜 DB·진짜 rag 검색(fake 임베딩)·D의 Harness를 쓰고, LLM만 규칙대로 답하는 모델로 바꾼다.
시나리오 A 4~12단계(금양 감사의견 거절 → Critical → 7명 → 승인·반려 → 업무 생성)를 서버 없이 확인한다.
"""
import asyncio

import pytest
from sqlalchemy import select

import shared.db as db_module
from agent import ApprovalBatch, PBContext, open_runtime
from agent.adapters.assembly import build_dependencies
from agent.adapters.harness_tools import recover_tool
from agent.harness_llm import prepare_input, recover_llm
from agent.rules import signal_input
from harness import recovery_llm as recovery
from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
from mcp_servers.business.tools.compute_customer_impact import compute_customer_impact
from mcp_servers.business.tools.create_contact_task import create_contact_task
from mcp_servers.business.tools.get_company_profile import get_company_profile
from mcp_servers.business.tools.get_disclosure import get_disclosure
from mcp_servers.business.tools.get_holders import get_holders
from mcp_servers.business.tools.get_signal_history import get_signal_history
from mcp_servers.business.tools.list_new_disclosures import list_new_disclosures
from mcp_servers.business.tools.save_assessment import save_assessment
from mcp_servers.rag import ingest, store
from mcp_servers.rag.embedding import embed
from mcp_servers.rag.tools import search_disclosure_text, search_reference
from shared.config import get_settings
from shared.models import AuditLog, ContactTask, Customer, RiskAssessment
from shared.schemas import ToolResponse

RCEPT = "20250321802001"
TOOLS = {f.__name__: f for f in (
    list_new_disclosures, get_disclosure, get_company_profile, get_signal_history, calculate_risk_score,
    get_holders, compute_customer_impact, save_assessment, create_contact_task,
    search_disclosure_text, search_reference,
)}


class DirectTools:
    """MCP 전송만 빼고 서버와 같은 함수를 부른다 (MCPTools와 같은 call 계약)."""

    async def call(self, name, arguments):
        return ToolResponse.model_validate(await asyncio.to_thread(TOOLS[name], **arguments))


class ScriptedModel:
    """입력으로 받은 조각·점수 내역만 인용하는 가짜 LLM."""

    def __init__(self):
        self.prompts = []

    async def generate(self, prompt, schema, payload):
        self.prompts.append((prompt, payload))
        if prompt == "select_tools":
            v = {"requests": []}
        elif prompt == "extract_facts":
            ch = next(c for c in payload["chunks"] if "의견거절" in c["text"])
            ev = {"source": ch["source"], "quote": "감사의견 의견거절", "rcept_no": ch["rcept_no"], "location": ch["location"]}
            v = {"facts": [{"signal_type": "AUDIT_OPINION", "values": {"opinion": "의견거절"}, "evidence": [ev]}]}
        elif prompt == "explain_risk":
            br = payload["score_result"]["breakdown"]
            v = {"items": [{"breakdown_index": i, "explanation": f"{b['item']} 항목", "evidence": b["evidence"]} for i, b in enumerate(br)]}
        else:
            msg = "{고객명}님, 금양 감사보고서 공시가 접수되었습니다. 내용을 확인하시고 PB와 상담해 보시기 바랍니다."
            v = {"drafts": {c["customer_id"]: msg for c in payload["customers"]}}
        return schema.model_validate(v)


@pytest.fixture()
def world(seeded_db, tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    get_settings.cache_clear()
    store.reset_client()
    path = next((ingest.RAG_DIR / "companies").glob("001570_*/20250321_AUDIT_REPORT_802001.md"))
    col = store.get_collection(store.DISCLOSURES, create=True)
    _, recs, _ = ingest.build_records(path, store.DISCLOSURES)
    col.upsert(ids=[r["id"] for r in recs], documents=[r["text"] for r in recs],
               metadatas=[r["meta"] for r in recs], embeddings=embed([r["text"] for r in recs]))
    ingest.run(only="reference", log=lambda *_: None)
    yield tmp_path / "ckpt.db"
    store.reset_client()
    get_settings.cache_clear()


def owners():
    with db_module.get_session() as s:
        return {c.customer_id: c.pb_id for c in s.scalars(select(Customer))}


def test_scenario_a_end_to_end_through_assembly(world):
    model = ScriptedModel()

    async def run():
        deps = await build_dependencies(tools=DirectTools(), model=model)
        async with open_runtime(deps, str(world)) as rt:
            st = await rt.start(RCEPT)
            assert st["status"] == "WAITING_APPROVAL" and st["risk_level"] == "Critical" and not st.get("error")
            assert st["evidence"] and st["ai_explanation"]
            pr = {p: sorted(c["customer_id"] for c in st["affected_customers"] if c["priority"] == p) for p in ("P1", "P2")}
            assert pr == {"P1": ["C001", "C003", "C005", "C006", "C007"], "P2": ["C002", "C004"]}
            assert len(st["drafts"]) == 7 and st["guardrail_flags"] == []
            aid = st["assessment_id"]

            own = owners()
            p01 = sorted(c for c in own if own[c] == "P01" and c in st["drafts"])
            dec = [{"customer_id": c, "status": "approved"} for c in p01]
            dec[0]["edited_message"] = "{고객명}님, 수정한 안내 문장입니다."
            st = await rt.resume(RCEPT, ApprovalBatch(decisions=dec), PBContext(pb_id="P01", allowed_customer_ids=frozenset(p01)))
            assert st["status"] == "WAITING_APPROVAL" and len(st["task_ids"]) == 4  # 시나리오 10단계

            p02 = sorted(c for c in own if own[c] == "P02" and c in st["drafts"])
            dec = [{"customer_id": c, "status": "rejected", "reject_reason": "고객 요청"} if c == "C002"
                   else {"customer_id": c, "status": "approved"} for c in p02]
            st = await rt.resume(RCEPT, ApprovalBatch(decisions=dec), PBContext(pb_id="P02", allowed_customer_ids=frozenset(p02)))
            assert st["status"] == "DONE"
            return aid, p01

    aid, p01 = asyncio.run(run())
    with db_module.get_session() as s:
        tasks = {t.customer_id: t for t in s.scalars(select(ContactTask))}
        assert sorted(tasks) == ["C001", "C003", "C004", "C005", "C006", "C007"]  # C002는 반려라 업무 없음
        assert all(t.status == "승인" and t.assessment_id == aid for t in tasks.values())
        assert tasks["C001"].draft_message == "{고객명}님, 수정한 안내 문장입니다."
        assert s.get(RiskAssessment, aid).status == "DONE"
        decisions = [a for a in s.scalars(select(AuditLog)) if a.action == "customer_decision"]
        assert any(a.payload.get("customer_id") == "C002" and a.payload.get("reject_reason") == "고객 요청" for a in decisions)
    # LLM 입력에는 실제 고객 이름이 없다
    names = {"한지우", "오민재", "정유나"}
    assert not any(n in str(payload) for _, payload in model.prompts for n in names)


def test_resume_after_restart_uses_same_thread(world):
    """서버를 껐다 켜는 것과 같다: 승인 대기에서 runtime을 닫고 새로 열어도 같은 thread_id로 이어진다."""

    async def run():
        deps = await build_dependencies(tools=DirectTools(), model=ScriptedModel())
        async with open_runtime(deps, str(world)) as rt:
            st = await rt.start(RCEPT)
            assert st["status"] == "WAITING_APPROVAL"
        deps2 = await build_dependencies(tools=DirectTools(), model=ScriptedModel())
        async with open_runtime(deps2, str(world)) as rt2:
            own = owners()
            p01 = sorted(c for c in own if own[c] == "P01" and c in st["drafts"])
            st2 = await rt2.resume(RCEPT, ApprovalBatch(decisions=[{"customer_id": c, "status": "approved"} for c in p01]),
                                   PBContext(pb_id="P01", allowed_customer_ids=frozenset(p01)))
            return st2

    st = asyncio.run(run())
    assert len(st["task_ids"]) == 4


def test_failure_before_scoring_is_saved_as_manual_check(world):
    class BadModel(ScriptedModel):
        async def generate(self, prompt, schema, payload):
            raise ValueError("구조 검증 실패")  # 사실 추출이 계속 실패 → 재시도 소진 → MANUAL_CHECK

    async def run():
        deps = await build_dependencies(tools=DirectTools(), model=BadModel())
        async with open_runtime(deps, str(world)) as rt:
            return await rt.start(RCEPT)

    st = asyncio.run(run())
    assert st["status"] == "MANUAL_CHECK"
    with db_module.get_session() as s:
        row = s.scalars(select(RiskAssessment)).one()
        assert row.status == "MANUAL_CHECK" and row.rcept_no == RCEPT


# ---------------------------------------------------------------- 조각별 단위 테스트
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


# ---------------------------------------------------------------- 모델 구성 (OpenAI 전용 포함)
def _settings(**kw):
    from types import SimpleNamespace

    base = dict(OPENAI_API_KEY="sk-test", ANTHROPIC_API_KEY="", LLM_PRIMARY="claude-haiku-5-5",
                LLM_FALLBACK="gpt-5.4-mini", FAKE_LLM_ERROR="")
    return SimpleNamespace(**{**base, **kw})


def _name(m):
    return getattr(m, "model_name", getattr(m, "model", None))


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
    from agent.state import DraftsOutput, FactsOutput

    m = _openai_chat("gpt-5.4-mini", "sk-test")
    for schema in (FactsOutput, DraftsOutput):
        bound = m.with_structured_output(schema)
        assert "tool" in str(bound.first.kwargs).lower() or "function" in str(bound.first.kwargs).lower()


# ---------------------------------------------------------------- 신호 없는 공시
def test_classification_skips_periodic_reports_and_keeps_ownership():
    from agent.rules import load_classifier
    from risk_engine.loader import DEFAULT_PATH

    classify = load_classifier(DEFAULT_PATH)
    assert classify("분기보고서 (2024.09)") is None and classify("[기재정정]사업보고서 (2023.12)") is None
    assert classify("주식등의대량보유상황보고서(일반)") == "OWNERSHIP"


def _ownership_disclosure(world):
    from mcp_servers.rag import ingest as ing

    path = next((ing.RAG_DIR / "companies").glob("001570_*/20240814_OWNERSHIP_004337.md"))
    col = store.get_collection(store.DISCLOSURES, create=True)
    _, recs, _ = ing.build_records(path, store.DISCLOSURES)
    col.upsert(ids=[r["id"] for r in recs], documents=[r["text"] for r in recs],
               metadatas=[r["meta"] for r in recs], embeddings=embed([r["text"] for r in recs]))
    return recs[0]["meta"]["rcept_no"]


class EmptyFactsModel(ScriptedModel):
    async def generate(self, prompt, schema, payload):
        if prompt == "extract_facts":
            return schema.model_validate({"facts": []})
        return await super().generate(prompt, schema, payload)


def test_ownership_without_signal_ends_quietly(world):
    rcept = _ownership_disclosure(world)

    async def run():
        deps = await build_dependencies(tools=DirectTools(), model=EmptyFactsModel())
        async with open_runtime(deps, str(world)) as rt:
            return await rt.start(rcept)

    st = asyncio.run(run())
    assert st["status"] == "DONE" and st.get("no_signal") is True and not st.get("error")
    with db_module.get_session() as s:
        assert s.scalars(select(RiskAssessment)).first() is None  # 평가·경보를 만들지 않는다


def test_empty_facts_for_other_types_still_need_manual_check(world):
    async def run():
        deps = await build_dependencies(tools=DirectTools(), model=EmptyFactsModel())
        async with open_runtime(deps, str(world)) as rt:
            return await rt.start(RCEPT)  # 감사보고서는 사실이 없으면 수동 확인

    st = asyncio.run(run())
    assert st["status"] == "MANUAL_CHECK" and not st.get("no_signal")


def test_unknown_signal_types_are_dropped_before_scoring(world):
    """AI가 공시 유형 이름(OWNERSHIP)을 signal_type으로 써도 규칙표 오류가 아니라 '신호 없음'이 된다."""
    rcept = _ownership_disclosure(world)

    class WrongTypeModel(ScriptedModel):
        async def generate(self, prompt, schema, payload):
            if prompt == "extract_facts":
                ch = payload["chunks"][0]
                ev = {"source": ch["source"], "quote": ch["text"][:20], "rcept_no": ch["rcept_no"], "location": ch["location"]}
                return schema.model_validate({"facts": [{"signal_type": "OWNERSHIP", "values": {}, "evidence": [ev]}]})
            return await super().generate(prompt, schema, payload)

    async def run():
        deps = await build_dependencies(tools=DirectTools(), model=WrongTypeModel())
        async with open_runtime(deps, str(world)) as rt:
            return await rt.start(rcept)

    st = asyncio.run(run())
    assert st["status"] == "DONE" and st.get("no_signal") is True


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


# ---------------------------------------------------------------- 인용 보정
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


def test_unsafe_draft_injector_only_touches_first_drafts():
    """FAKE_UNSAFE_DRAFT 시연: 첫 초안에만 금지 표현을 섞고, 다시 쓰기는 그대로 둔다 (가드레일이 잡고 다시 쓰게 하려는 것)."""
    from agent.model_adapter import UnsafeDraftInjector
    from agent.state import DraftsOutput
    from harness import guardrails

    class Inner:
        async def generate(self, prompt, schema, payload):
            return schema.model_validate({"drafts": {"C001": "{고객명}님, 공시를 안내드립니다."}})

    m = UnsafeDraftInjector(Inner())
    first = asyncio.run(m.generate("draft_message", DraftsOutput, {}))
    assert guardrails.find_forbidden(first.drafts["C001"])
    again = asyncio.run(m.generate("rewrite_message", DraftsOutput, {}))
    assert not guardrails.find_forbidden(again.drafts["C001"])


class RecordingTools:
    """호출을 기록하는 가짜 도구 (정리 단계 확인용)."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == self.fail:
            return ToolResponse.model_validate({"ok": False, "error": {"code": "DB_ERROR", "message": "x"}})
        data = {"save_assessment": {"assessment_id": 7}, "release_disclosure_index": {"released_chunks": 5, "content_sha256": "abc"},
                "purge_disclosure_text": {"purged": True}}[name]
        return ToolResponse.model_validate({"ok": True, "data": data})


class PassHarness:
    async def recover_tool(self, operation):
        return await operation()


def _save(status, mode, tools):
    from agent.adapters.store import AssessmentStore
    from agent.execution import Execution, current_execution

    async def run():
        token = current_execution.set(Execution(120, 8))
        try:
            store_ = AssessmentStore(tools, None, PassHarness(), upsert_confirmed=True)
            return await store_.save({"disclosure": {"corp_code": "C", "rcept_no": RCEPT}}, status)
        finally:
            current_execution.reset(token)

    import os
    os.environ["RAG_INDEX_MODE"] = mode
    get_settings.cache_clear()
    try:
        return asyncio.run(run())
    finally:
        os.environ.pop("RAG_INDEX_MODE", None)
        get_settings.cache_clear()


def test_ephemeral_mode_releases_index_and_body_after_the_alert_is_saved():
    tools = RecordingTools()
    assert _save("WAITING_APPROVAL", "ephemeral", tools) == 7
    assert [n for n, _ in tools.calls] == ["save_assessment", "release_disclosure_index", "purge_disclosure_text"]
    assert tools.calls[2][1] == {"rcept_no": RCEPT, "content_hash": "abc"}  # 해시는 색인 쪽이 알려 준 값을 넘긴다


def test_failed_or_persistent_runs_keep_the_text():
    for status, mode in (("MANUAL_CHECK", "ephemeral"), ("FAILED", "ephemeral"), ("WAITING_APPROVAL", "persistent")):
        tools = RecordingTools()
        _save(status, mode, tools)
        assert [n for n, _ in tools.calls] == ["save_assessment"], (status, mode)


def test_cleanup_failure_does_not_undo_the_saved_alert():
    tools = RecordingTools(fail="release_disclosure_index")
    assert _save("DONE", "ephemeral", tools) == 7           # 저장은 성공으로 끝난다
    assert [n for n, _ in tools.calls] == ["save_assessment", "release_disclosure_index"]
