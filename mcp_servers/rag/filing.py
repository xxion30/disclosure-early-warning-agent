"""운영(RAG_INDEX_MODE=ephemeral)의 공시별 메모리 색인.

공시 1건을 처음 검색할 때 **DART에서만** 원문(document.xml)을 받아 평문으로 만들고 그 조각을 메모리에 색인한다.
DB `disclosures.body_text`·`rag_docs/companies` 파일은 읽지 않는다 (원문은 저장하지 않는다). 제목·날짜·회사명 같은 메타데이터만 DB에서 읽는다.
원문 해시는 collector.dart_client가 한 곳에서 계산한다. 같은 원문이면 같은 조각·위치(location)가 나오므로 다시 받아도 인용이 맞는다.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Awaitable
from typing import TypeVar

from collector.dart_client import DartClient, DartError, DartRateLimited, DartUnavailable, Document
from mcp_servers.rag import ingest, memory, reference
from shared.config import get_settings
from shared.db import get_session
from shared.models import Company, Disclosure

log = logging.getLogger("rag-mcp")
T = TypeVar("T")

MAX_CHUNKS = 300  # 한 공시에서 색인하는 조각 수 상한(≈24만 자). 넘으면 앞부분만 색인하고 truncated로 알린다. 잠정값 (TODO 확인 필요)
INDEX = memory.Registry()


def is_ephemeral() -> bool:
    return get_settings().RAG_INDEX_MODE == "ephemeral"


def run_sync(coro: Awaitable[T]) -> T:
    """동기 도구 안에서 코루틴을 돌린다. 이미 이벤트 루프가 도는 스레드에서 불리면(비동기 테스트 등) 별도 스레드의 루프를 쓴다."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _fetch(rcept_no: str) -> Document:
    async with DartClient.from_settings() as client:
        return await client.fetch_document(rcept_no)


fetch_document = _fetch  # 테스트가 바꿔 끼운다


def _metadata(rcept_no: str) -> dict:
    """제목·접수일·회사명(원문 아님). 없으면 빈 dict — 출처 이름이 일반형으로 나온다."""
    try:
        with get_session() as s:
            d = s.get(Disclosure, rcept_no)
            if d is None:
                return {}
            c = s.get(Company, d.corp_code)
            return {"source": f"{c.name if c else ''} {d.title} ({d.filed_at})".strip(),
                    "stock_code": c.stock_code if c else "", "filed_at": d.filed_at, "title": d.title, "url": d.url or ""}
    except Exception:  # noqa: BLE001 — 메타데이터 때문에 검색이 실패하면 안 된다
        return {}


def build_entry(rcept_no: str) -> memory.Entry:
    """DART에서 원문을 받아 조각·벡터를 만든다. 실패는 LookupError(없음·받지 못함) 또는 TimeoutError(한도 초과·장애)로 올린다."""
    try:
        doc = run_sync(fetch_document(rcept_no))
    except (DartRateLimited, DartUnavailable) as e:
        raise TimeoutError(f"DART 원문 요청이 실패했습니다(재시도 소진): {e}") from None
    except (DartError, ValueError) as e:  # ValueError: DART_API_KEY가 비어 있음
        raise LookupError(f"원문을 받지 못했습니다: {e}") from None
    text = doc.text.strip()
    if not text:
        raise LookupError(f"접수번호 {rcept_no}의 원문이 비어 있습니다.")
    meta = _metadata(rcept_no)
    base = {"doc_id": rcept_no, "rcept_no": rcept_no, "source": meta.get("source") or f"DART {rcept_no}"}
    records, _ = ingest.records_from_body(rcept_no, text, base)
    truncated = len(records) > MAX_CHUNKS
    records = records[:MAX_CHUNKS]
    texts = [r["text"] for r in records]
    return memory.Entry(
        key=rcept_no, texts=texts, meta=[{"source": base["source"], "location": r["meta"]["location"]} for r in records],
        vectors=reference.embed_all(texts), sha256=doc.sha256, truncated=truncated,
    )


def search(rcept_no: str, query: str, k: int, holder: str | None = None) -> dict:
    """이 공시의 조각 중 질문과 가까운 k개. 색인이 없으면 만들고, 있으면 그대로 쓴다. 검색하는 동안 색인은 지워지지 않는다."""
    from mcp_servers.rag.embedding import embed

    with INDEX.lease(rcept_no, lambda: build_entry(rcept_no), holder) as entry:
        hits = entry.top(embed([query])[0], k)
        chunks = [{"source": entry.meta[i]["source"], "text": entry.texts[i], "rcept_no": rcept_no,
                   "location": entry.meta[i]["location"], "score": round(score, 4)} for score, i in hits]
        return {"count": len(chunks), "chunks": chunks, "content_sha256": entry.sha256, "truncated": entry.truncated}


def release(rcept_no: str, holder: str | None = None) -> dict:
    return INDEX.release(rcept_no, holder)
