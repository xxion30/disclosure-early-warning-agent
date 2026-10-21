"""운영(RAG_INDEX_MODE=ephemeral)의 공시별 임시 색인.

공시 1건을 처리할 때 원문을 읽어 그 공시의 조각만 색인에 넣고(ensure_indexed), 처리가 끝나면 지운다(release).
원문은 이 순서로 찾는다: ① DB disclosures.body_text → ② rag_docs/ 파일(데모·평가) → ③ DART (운영).
"""
from __future__ import annotations

import functools
import hashlib
from pathlib import Path

from sqlalchemy import select

from mcp_servers.rag import dart_source, ingest, store
from mcp_servers.rag.embedding import embed
from shared.config import get_settings
from shared.db import get_session
from shared.models import Company, Disclosure


def is_ephemeral() -> bool:
    return get_settings().RAG_INDEX_MODE == "ephemeral"


def _from_db(rcept_no: str) -> tuple[str, dict] | None:
    with get_session() as s:
        d = s.get(Disclosure, rcept_no)
        if d is None:
            return None
        company = s.get(Company, d.corp_code)
        meta = {
            "source": f"{company.name if company else ''} {d.title} ({d.filed_at})".strip(),
            "stock_code": company.stock_code if company else "", "filed_at": d.filed_at, "title": d.title, "url": d.url or "",
        }
        return (d.body_text or ""), meta


@functools.lru_cache(maxsize=1)
def _file_index() -> dict[str, Path]:
    """rag_docs/companies/**.md 머리말의 rcept_no → 파일 경로 (처음 한 번만 읽는다)."""
    out: dict[str, Path] = {}
    for path in (ingest.RAG_DIR / "companies").glob("*/*.md"):
        try:
            meta, _ = ingest.parse_doc(path)
        except ValueError:
            continue
        if meta.get("rcept_no"):
            out[meta["rcept_no"]] = path
    return out


def _from_file(rcept_no: str) -> tuple[str, dict] | None:
    path = _file_index().get(rcept_no)
    if path is None:
        return None
    meta, body = ingest.parse_doc(path)
    return body, {"source": f"{meta.get('corp_name', '')} {meta.get('title', '')} ({meta.get('filed_at', '')})".strip(),
                  "stock_code": meta.get("stock_code", ""), "filed_at": meta.get("filed_at", ""),
                  "title": meta.get("title", ""), "url": meta.get("url", "")}


def load_filing(rcept_no: str) -> tuple[str, dict, str]:
    """(본문, 조각 메타, 출처 이름: db|file|dart). 어디에도 없으면 LookupError."""
    row = _from_db(rcept_no)
    if row and row[0].strip():
        return row[0].strip(), row[1], "db"
    hit = _from_file(rcept_no)
    if hit and hit[0].strip():
        return hit[0].strip(), hit[1], "file"
    text = dart_source.fetch_text(rcept_no)
    if not text.strip():
        raise LookupError(f"접수번호 {rcept_no}의 원문을 DB·파일·DART 어디에서도 찾지 못했습니다.")
    meta = row[1] if row else {"source": f"DART {rcept_no}", "stock_code": "", "filed_at": "", "title": "", "url": ""}
    return text.strip(), meta, "dart"


def ensure_indexed(rcept_no: str) -> None:
    """이 공시의 조각이 색인에 없으면 원문을 읽어 만든다. 이미 있으면 아무것도 하지 않는다."""
    col = store.get_collection(store.DISCLOSURES, create=True)
    if col.get(where={"rcept_no": rcept_no}, limit=1)["ids"]:
        return
    body, meta, _origin = load_filing(rcept_no)
    base = {"doc_id": rcept_no, "rcept_no": rcept_no, **meta, "content_sha256": hashlib.sha256(body.encode()).hexdigest()}
    records, _ = ingest.records_from_body(rcept_no, body, base)
    for i in range(0, len(records), 64):
        part = records[i:i + 64]
        col.upsert(ids=[r["id"] for r in part], documents=[r["text"] for r in part],
                   metadatas=[r["meta"] for r in part], embeddings=embed([r["text"] for r in part]))


def release(rcept_no: str) -> dict:
    """이 공시의 조각을 색인에서 지운다. 원문 sha256은 DB에 남기려고 돌려준다."""
    try:
        col = store.get_collection(store.DISCLOSURES)
    except Exception:  # noqa: BLE001 — 색인 자체가 없으면 지울 것도 없다
        return {"rcept_no": rcept_no, "released_chunks": 0, "content_sha256": None}
    got = col.get(where={"rcept_no": rcept_no}, include=["metadatas"])
    sha = next((m.get("content_sha256") for m in got["metadatas"] if m.get("content_sha256")), None)
    if got["ids"]:
        col.delete(ids=got["ids"])
    return {"rcept_no": rcept_no, "released_chunks": len(got["ids"]), "content_sha256": sha}
