"""rag-mcp 테스트: fake 임베딩(네트워크·키 없음)으로 실제 rag_docs 일부를 색인한다."""
import asyncio

import pytest

import shared.db  # noqa: F401
from mcp_servers.rag import ingest, store
from shared.config import get_settings

KY_AUDIT = "20250321802001"  # 금양 감사보고서제출 (감사의견 의견거절)


@pytest.fixture(scope="module")
def rag(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("EMBEDDING_PROVIDER", "fake")
    mp.setenv("CHROMA_DIR", str(tmp_path_factory.mktemp("chroma")))
    get_settings.cache_clear()
    store.reset_client()
    stats = ingest.run(only="reference", log=lambda *_: None)
    # 금양 감사보고서 1건만 회사 문서로 추가 (전체 적재는 느리다)
    path = next((ingest.RAG_DIR / "companies").glob("001570_*/20250321_AUDIT_REPORT_802001.md"))
    col = store.get_collection(store.DISCLOSURES, create=True)
    from mcp_servers.rag.embedding import embed

    _, records, _ = ingest.build_records(path, store.DISCLOSURES)
    col.upsert(ids=[r["id"] for r in records], documents=[r["text"] for r in records],
               metadatas=[r["meta"] for r in records], embeddings=embed([r["text"] for r in records]))
    yield stats
    mp.undo()
    get_settings.cache_clear()
    store.reset_client()


def test_chunker_size_and_overlap():
    text = "가" * 2000
    chunks = store.chunk_text(text)
    assert [(s, e) for s, e, _ in chunks] == [(0, 800), (700, 1500), (1400, 2000)]
    assert all(len(t) <= 800 for _, _, t in chunks)
    assert store.chunk_text("   ") == []


def test_reference_ingested_with_categories(rag):
    assert rag["errors"] == [] and rag["docs"] == 4


def test_disclosure_search_returns_agent_chunk_shape(rag):
    from mcp_servers.rag.tools import search_disclosure_text

    res = search_disclosure_text(KY_AUDIT, "감사의견 의견거절", 5)
    assert res["ok"] and res["data"]["count"] >= 1
    for c in res["data"]["chunks"]:
        # A의 agent/evidence.py chunks_from()이 요구하는 네 필드는 비어 있지 않은 문자열
        assert all(isinstance(c[k], str) and c[k].strip() for k in ("source", "text", "rcept_no", "location"))
        assert c["rcept_no"] == KY_AUDIT and len(c["text"]) <= 800
    assert any("의견거절" in c["text"] for c in res["data"]["chunks"])


def test_agent_chunks_from_accepts_result(rag):
    from agent.evidence import chunks_from
    from mcp_servers.rag.tools import search_disclosure_text

    chunks = chunks_from(search_disclosure_text(KY_AUDIT, "감사의견", 5)["data"])
    assert 1 <= len(chunks) <= 5


def test_disclosure_search_errors(rag):
    from mcp_servers.rag.tools import search_disclosure_text

    assert search_disclosure_text("99999999999999", "x")["error"]["code"] == "NOT_FOUND"
    assert search_disclosure_text("abc", "x")["error"]["code"] == "INVALID_INPUT"
    assert search_disclosure_text(KY_AUDIT, "  ")["error"]["code"] == "INVALID_INPUT"
    assert search_disclosure_text(KY_AUDIT, "x", 0)["error"]["code"] == "INVALID_INPUT"


def test_reference_search_by_category(rag):
    from mcp_servers.rag.tools import search_reference

    res = search_reference("안내문 작성 원칙", "안내문가이드", 3)
    assert res["ok"] and all(c["category"] == "안내문가이드" for c in res["data"]["chunks"])
    assert all("rcept_no" not in c for c in res["data"]["chunks"])
    assert search_reference("x", "없는분류")["error"]["code"] == "INVALID_INPUT"


def test_reingest_skips_unchanged_documents(rag):
    again = ingest.run(only="reference", log=lambda *_: None)
    assert again["skipped_docs"] == 4 and again["embedded_chunks"] == 0


def test_server_registers_two_tools(rag):
    from fastmcp import Client

    from mcp_servers.rag.server import mcp

    async def run():
        async with Client(mcp) as c:
            names = {t.name for t in await c.list_tools()}
            r = await c.call_tool("search_disclosure_text", {"rcept_no": KY_AUDIT, "query": "감사의견"})
            return names, r.data

    names, data = asyncio.run(run())
    assert names == {"search_disclosure_text", "search_reference"} and data["ok"] is True
