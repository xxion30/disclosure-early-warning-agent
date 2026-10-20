"""Chroma 저장소 (로컬 파일, CHROMA_DIR). 컬렉션 2개: disclosures(공시 원문), reference(참고 문서)."""
from __future__ import annotations

from pathlib import Path

from mcp_servers.rag.embedding import provider_tag
from shared.config import ROOT_DIR, get_settings

DISCLOSURES = "disclosures"
REFERENCE = "reference"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

_client = None


def chroma_dir() -> Path:
    p = Path(get_settings().CHROMA_DIR)
    p = p if p.is_absolute() else ROOT_DIR / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_client():
    global _client
    if _client is None:
        import chromadb

        _client = chromadb.PersistentClient(path=str(chroma_dir()))
    return _client


def reset_client() -> None:
    global _client
    _client = None


def get_collection(name: str, *, create: bool = False):
    client = get_client()
    meta = {"hnsw:space": "cosine", "embedding": provider_tag()}
    if create:
        col = client.get_or_create_collection(name, metadata=meta)
    else:
        col = client.get_collection(name)
    stored = (col.metadata or {}).get("embedding")
    if stored and stored != provider_tag():
        raise RuntimeError(
            f"색인은 {stored}로 만들었는데 지금 설정은 {provider_tag()}입니다. "
            "같은 설정으로 쓰거나 `python -m mcp_servers.rag.ingest --reset`로 다시 만드세요."
        )
    return col


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[tuple[int, int, str]]:
    """(시작, 끝, 조각). 조각 크기 800자, 겹침 100자 (CLAUDE.md 4-3장)."""
    text = text.strip()
    out, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        piece = text[start:end].strip()
        if piece:
            out.append((start, end, piece))
        if end == len(text):
            break
        start = end - overlap
    return out
