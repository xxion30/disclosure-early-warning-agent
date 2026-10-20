"""임베딩. EMBEDDING_PROVIDER = openai | local | fake (CLAUDE.md 1장).

fake는 네트워크·키 없이 도는 해시 기반 임베딩이다. 테스트와 키가 없을 때의 점검용이며 의미 검색 품질은 낮다.
"""
from __future__ import annotations

import hashlib
import math

from shared.config import get_settings

FAKE_DIM = 256
LOCAL_MODEL = "BAAI/bge-m3"
BATCH = 100


def provider_tag() -> str:
    """색인을 만들 때 쓴 임베딩 설정. 질의 때 다르면 벡터 공간이 달라 검색이 틀리므로 비교한다."""
    s = get_settings()
    if s.EMBEDDING_PROVIDER == "openai":
        return f"openai:{s.EMBEDDING_MODEL}"
    if s.EMBEDDING_PROVIDER == "local":
        return f"local:{LOCAL_MODEL}"
    return f"fake:{FAKE_DIM}"


def _fake(text: str) -> list[float]:
    vec = [0.0] * FAKE_DIM
    t = "".join(text.split())
    for i in range(len(t) - 1):
        h = int(hashlib.md5(t[i : i + 2].encode("utf-8")).hexdigest(), 16)
        vec[h % FAKE_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    if s.EMBEDDING_PROVIDER == "fake":
        return [_fake(t) for t in texts]
    if s.EMBEDDING_PROVIDER == "local":
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError("EMBEDDING_PROVIDER=local은 sentence-transformers 설치가 필요합니다.") from e
        model = _local_model(SentenceTransformer)
        return [v.tolist() for v in model.encode(texts, normalize_embeddings=True)]
    if s.EMBEDDING_PROVIDER != "openai":
        raise RuntimeError(f"알 수 없는 EMBEDDING_PROVIDER: {s.EMBEDDING_PROVIDER!r} (openai | local | fake)")
    if not s.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 .env에 없습니다.")
    from langchain_openai import OpenAIEmbeddings

    client = OpenAIEmbeddings(model=s.EMBEDDING_MODEL, api_key=s.OPENAI_API_KEY, chunk_size=BATCH)
    return client.embed_documents(texts)


_local = None


def _local_model(cls):
    global _local
    if _local is None:
        _local = cls(LOCAL_MODEL)
    return _local
