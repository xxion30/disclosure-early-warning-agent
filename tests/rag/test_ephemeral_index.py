"""운영 모드(RAG_INDEX_MODE=ephemeral): 공시마다 필요할 때 임시 색인을 만든다 (DB 본문 → 파일 → DART). (담당: R3)

처리 후 삭제(release·purge)는 도구를 삭제해 테스트도 뺐다. 메모리 안 임시 색인으로 바꿀 때 다시 쓴다."""
import asyncio
import hashlib

import pytest
from sqlalchemy import select

import shared.db as db_module
from mcp_servers.rag import filing, store
from mcp_servers.rag.tools import search_disclosure_text
from shared.config import get_settings
from shared.models import Disclosure

RCEPT = "20250321802001"  # 금양 감사보고서제출
BODY = "감사의견 의견거절. " + "회사의 계속기업 존속능력에 대한 중요한 불확실성이 있습니다. " * 60


@pytest.fixture()
def ephemeral(seeded_db, tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("RAG_INDEX_MODE", "ephemeral")
    get_settings.cache_clear()
    store.reset_client()
    with db_module.get_session() as s:
        s.get(Disclosure, RCEPT).body_text = BODY
    yield
    store.reset_client()
    get_settings.cache_clear()


def _blank_body():
    with db_module.get_session() as s:
        s.get(Disclosure, RCEPT).body_text = None


def chunk_count():
    try:
        return len(store.get_collection(store.DISCLOSURES).get(where={"rcept_no": RCEPT})["ids"])
    except Exception:  # noqa: BLE001
        return 0


def test_index_is_built_on_demand_from_the_db_text(ephemeral):
    assert chunk_count() == 0                         # 미리 적재하지 않는다
    res = search_disclosure_text(RCEPT, "감사의견 의견거절", 3)
    assert res["ok"] and res["data"]["count"] == 3 and chunk_count() >= 3
    assert all(c["rcept_no"] == RCEPT and c["location"].startswith("chunk-") for c in res["data"]["chunks"])


def test_without_a_stored_body_the_text_is_fetched_from_dart(ephemeral, monkeypatch):
    _blank_body()
    monkeypatch.setattr(filing, "_from_file", lambda rcept: None)  # 운영에는 rag_docs 파일이 없다
    monkeypatch.setattr("mcp_servers.rag.dart_source.fetch_text", lambda rcept, timeout=60: BODY)
    res = search_disclosure_text(RCEPT, "계속기업", 2)
    assert res["ok"] and res["data"]["count"] == 2
    assert all(c["source"].startswith("금양") for c in res["data"]["chunks"])  # 제목·회사 메타는 DB에서 온다


def test_dart_failure_is_returned_as_a_tool_error_not_an_exception(ephemeral, monkeypatch):
    _blank_body()
    monkeypatch.setattr(filing, "_from_file", lambda rcept: None)

    def boom(rcept, timeout=60):
        raise RuntimeError("DART_API_KEY가 .env에 없어 원문을 받을 수 없습니다.")

    monkeypatch.setattr("mcp_servers.rag.dart_source.fetch_text", boom)
    res = search_disclosure_text(RCEPT, "계속기업", 2)
    assert res["ok"] is False and res["error"]["code"] == "NOT_FOUND"


def test_new_columns_are_added_to_an_existing_database(tmp_path, monkeypatch):
    """이미 만들어진 data/app.db에 새 칸이 없어도 init_db가 채워 넣는다 (기존 데이터는 그대로)."""
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE companies (corp_code TEXT PRIMARY KEY, stock_code TEXT, name TEXT, market TEXT, base_score FLOAT, base_reasons JSON)")
    con.execute("CREATE TABLE disclosures (rcept_no TEXT PRIMARY KEY, corp_code TEXT, title TEXT, disclosure_type TEXT, filed_at TEXT, url TEXT, body_text TEXT, processed BOOLEAN)")
    con.execute("INSERT INTO disclosures VALUES ('1','C','제목',NULL,'2025-01-01',NULL,'본문',0)")
    con.commit(); con.close()
    monkeypatch.setenv("DB_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    db_module._engine = None
    db_module._SessionLocal = None
    db_module.init_db()
    with db_module.get_session() as s:
        d = s.scalars(select(Disclosure)).one()
        assert d.body_text == "본문" and d.content_hash is None and d.purged_at is None
    db_module._engine = None
    db_module._SessionLocal = None
    get_settings.cache_clear()
