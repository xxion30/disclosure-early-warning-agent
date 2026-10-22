"""agent 조립 전체 흐름 테스트 (담당: R4, 모델·인용 쪽은 R2와 협업). 모델 쪽 단위 테스트는 tests/agent/test_model_adapter.py.

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
    get_holders, save_assessment, create_contact_task,
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
            assert st["status"] == "WAITING_APPROVAL" and st["risk_level"] == "High" and not st.get("error")
            assert st["evidence"] and st["ai_explanation"]
            assert sorted(c["customer_id"] for c in st["affected_customers"]) == [f"C00{i}" for i in range(1, 8)]
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


# ---------------------------------------------------------------- 모델 구성 (OpenAI 전용 포함)


# ---------------------------------------------------------------- 신호 없는 공시


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


# ---------------------------------------------------------------- 인용 보정


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
