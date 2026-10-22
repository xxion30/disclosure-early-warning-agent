"""rag-mcp 도구 2개 (CLAUDE.md 4-3장). 결과마다 출처(source, rcept_no, location)를 돌려준다 — 근거 인용에 쓴다."""
from __future__ import annotations

import functools
import logging

import requests

from mcp_servers.rag import filing, store
from mcp_servers.rag.embedding import embed
from mcp_servers.rag.ingest import CATEGORIES
from shared.schemas import ErrorCode, fail, ok

log = logging.getLogger("rag-mcp")
MAX_K = 10


class ToolFail(Exception):
    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def guarded(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolFail as e:
            return fail(e.code, e.message)
        except TimeoutError as e:
            return fail(ErrorCode.TIMEOUT, str(e) or "시간 초과")
        except Exception as e:  # noqa: BLE001 — 도구는 예외를 던지지 않는다
            log.exception("검색 오류")
            return fail(ErrorCode.DB_ERROR, f"검색 중 오류: {type(e).__name__}: {e}")

    return wrapper


def _k(k) -> int:
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
        raise ToolFail(ErrorCode.INVALID_INPUT, f"k는 1~{MAX_K} 사이의 정수여야 합니다.")
    return k


def _query(q) -> str:
    if not isinstance(q, str) or not q.strip():
        raise ToolFail(ErrorCode.INVALID_INPUT, "query가 비어 있습니다.")
    return q.strip()


def _search(collection: str, query: str, k: int, where: dict, empty_message: str) -> list[dict]:
    try:
        col = store.get_collection(collection)
    except RuntimeError:
        raise
    except Exception as e:  # 컬렉션 없음 = 아직 적재 전
        raise ToolFail(ErrorCode.NOT_FOUND, f"색인이 없습니다. `python -m mcp_servers.rag.ingest`를 먼저 실행하세요. ({type(e).__name__})") from e
    if not col.get(where=where, limit=1)["ids"]:
        raise ToolFail(ErrorCode.NOT_FOUND, empty_message)
    res = col.query(query_embeddings=embed([query]), n_results=k, where=where, include=["documents", "metadatas", "distances"])
    out = []
    for text, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        chunk = {"source": meta["source"], "text": text, "location": meta["location"], "score": round(1 - dist, 4)}
        if meta.get("rcept_no"):
            chunk["rcept_no"] = meta["rcept_no"]
        if meta.get("category"):
            chunk["category"] = meta["category"]
        out.append(chunk)
    return out


@guarded
def search_disclosure_text(rcept_no: str, query: str, k: int = 5) -> dict:
    """한 공시의 원문에서 질문과 가까운 조각을 찾는다. 다른 공시의 조각은 섞이지 않는다.

    Args:
        rcept_no: 접수번호 (숫자)
        query: 찾고 싶은 내용. 예: "감사의견 의견거절 사유"
        k: 돌려줄 조각 수 (1~10, 기본 5)

    Returns:
        chunks: [{source, text, rcept_no, location, score}] — text는 조각 전문이므로 인용은 text 안의 연속된 문장에서 한다.
    """
    if not isinstance(rcept_no, str) or not rcept_no.isdigit():
        raise ToolFail(ErrorCode.INVALID_INPUT, "rcept_no는 숫자로 된 접수번호여야 합니다.")
    if filing.is_ephemeral():
        # 운영: 이 공시의 조각이 아직 없으면 원문(DB → 파일 → DART)으로 임시 색인을 만든다
        try:
            filing.ensure_indexed(rcept_no)
        except LookupError as e:
            raise ToolFail(ErrorCode.NOT_FOUND, str(e)) from e
        except requests.Timeout as e:
            raise ToolFail(ErrorCode.TIMEOUT, "DART 원문 요청 시간이 초과됐습니다.") from e
        except (requests.RequestException, RuntimeError) as e:
            raise ToolFail(ErrorCode.NOT_FOUND, f"원문을 받지 못했습니다: {e}") from e
    chunks = _search(
        store.DISCLOSURES, _query(query), _k(k), {"rcept_no": rcept_no},
        f"접수번호 {rcept_no}의 원문이 색인되어 있지 않습니다 (본문이 없는 공시이거나 아직 적재 전).",
    )
    return ok({"rcept_no": rcept_no, "count": len(chunks), "chunks": chunks})


@guarded
def search_reference(query: str, category: str, k: int = 5) -> dict:
    """참고 문서(상장규정·공시해설·회계용어·안내문가이드)에서 질문과 가까운 조각을 찾는다.

    Args:
        query: 찾고 싶은 내용
        category: 상장규정 | 공시해설 | 회계용어 | 안내문가이드
        k: 돌려줄 조각 수 (1~10, 기본 5)

    Returns:
        chunks: [{source, text, location, category, score}]
    """
    if category not in CATEGORIES:
        raise ToolFail(ErrorCode.INVALID_INPUT, f"category는 {', '.join(CATEGORIES)} 중 하나여야 합니다: {category!r}")
    chunks = _search(store.REFERENCE, _query(query), _k(k), {"category": category}, f"'{category}' 문서가 색인되어 있지 않습니다.")
    return ok({"category": category, "count": len(chunks), "chunks": chunks})
