"""참고 문서(rag_docs/reference/)의 메모리 색인. 처음 쓸 때(또는 서버 시작 때 warm) 한 번 만들고 디스크에 쓰지 않는다.

임베딩 설정(EMBEDDING_PROVIDER 등)이 바뀌면 벡터 공간이 달라지므로 설정이 바뀌면 다시 만든다.
"""
from __future__ import annotations

import logging
import threading

from mcp_servers.rag import ingest, memory, store
from mcp_servers.rag.embedding import BATCH, embed, provider_tag

log = logging.getLogger("rag-mcp")

_lock = threading.Lock()
_index: memory.Entry | None = None
_tag: str | None = None


def reset() -> None:
    global _index, _tag
    with _lock:
        _index, _tag = None, None


def embed_all(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        vectors += embed(texts[i : i + BATCH])
    return vectors


def get() -> memory.Entry:
    """참고 문서 색인. 없으면 만든다. 문서가 하나도 없으면 빈 색인이다."""
    global _index, _tag
    with _lock:
        tag = provider_tag()
        if _index is None or _tag != tag:
            records = []
            for path in sorted((ingest.RAG_DIR / "reference").glob("*.md")):
                try:
                    records += ingest.build_records(path, store.REFERENCE)[1]
                except ValueError as e:  # 머리말이 틀린 문서는 건너뛴다
                    log.warning("참고 문서 건너뜀: %s", e)
            texts = [r["text"] for r in records]
            _index = memory.Entry(
                key="reference", texts=texts, meta=[r["meta"] for r in records], vectors=embed_all(texts) if texts else [],
            )
            _tag = tag
        return _index


def search(query: str, category: str, k: int) -> list[dict] | None:
    """category의 조각 중 질문과 가까운 k개. 그 category 문서가 없으면 None."""
    entry = get()
    if not any(m.get("category") == category for m in entry.meta):
        return None
    out = []
    for score, i in entry.top(embed([query])[0], k, where=lambda m: m.get("category") == category):
        meta = entry.meta[i]
        out.append({"source": meta["source"], "text": entry.texts[i], "location": meta["location"],
                    "category": meta["category"], "score": round(score, 4)})
    return out


def warm() -> bool:
    """서버를 띄울 때 미리 만든다 (첫 요청이 느려지지 않게). 임베딩 키가 없는 등 실패하면 로그만 남기고 False."""
    try:
        get()
        return True
    except Exception as e:  # noqa: BLE001 — 시작을 막지 않는다. 첫 검색 때 다시 시도한다
        log.warning("참고 문서 색인을 미리 만들지 못함: %s: %s", type(e).__name__, e)
        return False
