"""agent 조립 전체 흐름 테스트 (담당: R4, 모델·인용 쪽은 R2와 협업). 모델 쪽 단위 테스트는 tests/agent/test_model_adapter.py.

진짜 B 도구 함수·진짜 DB·진짜 rag 검색(fake 임베딩)·D의 Harness를 쓰고, LLM만 규칙대로 답하는 모델로 바꾼다.
금양 감사의견 거절 공시가 분석 → 설명 → 저장 → DONE까지 끝나는지 서버 없이 확인한다.
고객 연락(안내문·승인·업무)은 삭제되어 이 흐름에 없다.
"""
import asyncio

import pytest
from sqlalchemy import select

import shared.db as db_module
from agent import open_runtime
from agent.adapters.assembly import build_dependencies
from agent.adapters.harness_tools import recover_tool
from agent.harness_llm import prepare_input, recover_llm
from agent.rules import signal_input
from harness import recovery_llm as recovery
from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
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
from shared.models import RiskAssessment
from shared.schemas import ToolResponse

RCEPT = "20250321802001"
TOOLS = {f.__name__: f for f in (
    list_new_disclosures, get_disclosure, get_company_profile, get_signal_history, calculate_risk_score,
    get_holders, save_assessment,
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
        else:  # explain_risk
            br = payload["score_result"]["breakdown"]
            v = {"items": [{"breakdown_index": i, "explanation": f"{b['item']} 항목", "evidence": b["evidence"]} for i, b in enumerate(br)]}
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


def test_critical_disclosure_end_to_end_through_assembly(world):
    """감사의견 거절 공시: 사실 추출 → 규칙 점수(확정 악재 Critical) → 설명 → 저장 → DONE."""
    model = ScriptedModel()

    async def run():
        deps = await build_dependencies(tools=DirectTools(), model=model)
        async with open_runtime(deps, str(world)) as rt:
            return await rt.start(RCEPT)

    st = asyncio.run(run())
    assert st["status"] == "DONE" and st["risk_level"] == "Critical" and not st.get("error")
    assert st["evidence"] and st["ai_explanation"]
    with db_module.get_session() as s:
        row = s.scalars(select(RiskAssessment)).one()
        assert row.rcept_no == RCEPT and row.level == "Critical" and row.status == "DONE"
        assert row.assessment_id == st["assessment_id"]
    assert not [p for p, _ in model.prompts if p in ("draft_message", "rewrite_message")]


def test_restart_keeps_the_saved_assessment_and_thread(world):
    """서버를 껐다 켜도 같은 thread_id의 결과가 그대로이고 다시 분석하지 않는다."""

    async def run():
        deps = await build_dependencies(tools=DirectTools(), model=ScriptedModel())
        async with open_runtime(deps, str(world)) as rt:
            first = await rt.start(RCEPT)
        model2 = ScriptedModel()
        deps2 = await build_dependencies(tools=DirectTools(), model=model2)
        async with open_runtime(deps2, str(world)) as rt2:
            again = await rt2.start(RCEPT)
        return first, again, model2

    first, again, model2 = asyncio.run(run())
    assert again == first and again["assessment_id"] == first["assessment_id"]
    assert not model2.prompts  # 모델을 다시 부르지 않았다


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

