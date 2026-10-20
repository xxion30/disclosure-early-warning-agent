"""agent.assembly 조립 테스트.

진짜 B 도구 함수·진짜 DB·진짜 rag 검색(fake 임베딩)·D의 Harness를 쓰고, LLM만 규칙대로 답하는 모델로 바꾼다.
시나리오 A 4~12단계(금양 감사의견 거절 → Critical → 7명 → 승인·반려 → 업무 생성)를 서버 없이 확인한다.
"""
import asyncio

import pytest
from sqlalchemy import select

import shared.db as db_module
from agent import ApprovalBatch, PBContext, open_runtime
from agent.assembly import build_dependencies, prepare_input, recover_llm, recover_tool, signal_input
from harness import recovery
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
        if prompt == "extract_facts":
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
    monkeypatch.setattr("agent.assembly._audit_system", noop)

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

    monkeypatch.setattr("agent.assembly._audit_system", noop)
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
    from agent.assembly import build_model

    m = build_model(None, _settings())
    assert _name(m.primary) == "gpt-5.4-mini" and _name(m.fallback) == "gpt-5.4-mini"


def test_model_openai_primary_by_name():
    from agent.assembly import build_model

    m = build_model(None, _settings(LLM_PRIMARY="gpt-5.4", ANTHROPIC_API_KEY="x"))
    assert _name(m.primary) == "gpt-5.4" and _name(m.fallback) == "gpt-5.4-mini"


def test_model_anthropic_primary_when_key_present():
    from agent.assembly import build_model

    m = build_model(None, _settings(ANTHROPIC_API_KEY="sk-ant-test"))
    assert _name(m.primary) == "claude-haiku-5-5" and _name(m.fallback) == "gpt-5.4-mini"


def test_model_requires_openai_key():
    from agent.assembly import build_model

    with pytest.raises(ValueError):
        build_model(None, _settings(OPENAI_API_KEY=""))


def test_openai_models_use_function_calling_for_structured_output():
    """strict json_schema는 dict[str, Any]를 거부하므로 function calling을 기본으로 쓴다."""
    from agent.assembly import _openai_chat
    from agent.state import DraftsOutput, FactsOutput

    m = _openai_chat("gpt-5.4-mini", "sk-test")
    for schema in (FactsOutput, DraftsOutput):
        bound = m.with_structured_output(schema)
        assert "tool" in str(bound.first.kwargs).lower() or "function" in str(bound.first.kwargs).lower()
