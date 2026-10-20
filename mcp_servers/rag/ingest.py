"""rag_docs/ → Chroma 적재.

    uv run python -m mcp_servers.rag.ingest              # 새로 바뀐 문서만 임베딩해서 추가
    uv run python -m mcp_servers.rag.ingest --reset      # 컬렉션을 지우고 처음부터
    uv run python -m mcp_servers.rag.ingest --dry-run    # 조각 수와 글자 수만 센다 (임베딩 호출 없음)

문서 머리말(---)의 rcept_no·corp_name·stock_code·filed_at·title(회사 공시) / category·title(참고 문서)를 조각 메타데이터로 남긴다.
같은 문서를 다시 돌려도 내용이 같으면 건너뛴다(비용 절약). 내용이 바뀌면 그 문서의 조각만 지우고 다시 넣는다.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

import yaml

from mcp_servers.rag import store
from mcp_servers.rag.embedding import BATCH, embed, provider_tag
from shared.config import ROOT_DIR

RAG_DIR = ROOT_DIR / "rag_docs"
CATEGORIES = ("상장규정", "공시해설", "회계용어", "안내문가이드")
FRONT = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)


def parse_doc(path: Path) -> tuple[dict, str]:
    m = FRONT.match(path.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{path.name}: 머리말(---)이 없습니다.")
    meta = yaml.safe_load(m.group(1)) or {}
    return {k: ("" if v is None else str(v)) for k, v in meta.items()}, m.group(2).strip()


def location_of(i: int, start: int, end: int) -> str:
    return f"chunk-{i} ({start}-{end}자)"


def build_records(path: Path, kind: str) -> tuple[str, list[dict], str]:
    """(doc_id, 조각 레코드들, 본문 해시)."""
    meta, body = parse_doc(path)
    if kind == store.DISCLOSURES:
        rcept = meta.get("rcept_no", "")
        if not rcept.isdigit():
            raise ValueError(f"{path.name}: rcept_no가 숫자가 아닙니다 ({rcept!r}).")
        doc_id = rcept
        source = f"{meta.get('corp_name', '')} {meta.get('title', '')} ({meta.get('filed_at', '')})".strip()
        base = {
            "doc_id": doc_id, "rcept_no": rcept, "source": source, "stock_code": meta.get("stock_code", ""),
            "filed_at": meta.get("filed_at", ""), "title": meta.get("title", ""), "url": meta.get("url", ""),
        }
    else:
        category = meta.get("category", "")
        if category not in CATEGORIES:
            raise ValueError(f"{path.name}: category는 {', '.join(CATEGORIES)} 중 하나여야 합니다 ({category!r}).")
        doc_id = path.stem
        base = {"doc_id": doc_id, "source": meta.get("title", path.stem), "category": category, "title": meta.get("title", "")}
    doc_hash = hashlib.sha256(f"{store.CHUNK_SIZE}/{store.CHUNK_OVERLAP}/{body}".encode()).hexdigest()[:16]
    records = []
    for i, (start, end, text) in enumerate(store.chunk_text(body)):
        records.append(
            {"id": f"{doc_id}:{i}", "text": text, "meta": {**base, "location": location_of(i, start, end), "chunk_index": i, "doc_hash": doc_hash}}
        )
    return doc_id, records, doc_hash


def collect(only: str | None) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    if only in (None, "companies"):
        files += [(p, store.DISCLOSURES) for p in sorted((RAG_DIR / "companies").glob("*/*.md"))]
    if only in (None, "reference"):
        files += [(p, store.REFERENCE) for p in sorted((RAG_DIR / "reference").glob("*.md"))]
    return files


def run(*, reset: bool = False, dry_run: bool = False, only: str | None = None, log=print) -> dict:
    files = collect(only)
    stats = {"docs": 0, "chunks": 0, "chars": 0, "skipped_docs": 0, "embedded_chunks": 0, "errors": []}
    cols = {}
    if not dry_run:
        client = store.get_client()
        if reset:
            for name in (store.DISCLOSURES, store.REFERENCE):
                if only is None or (only == "companies") == (name == store.DISCLOSURES):
                    try:
                        client.delete_collection(name)
                    except Exception:  # noqa: BLE001 — 없으면 그대로 진행
                        pass
        cols = {n: store.get_collection(n, create=True) for n in (store.DISCLOSURES, store.REFERENCE)}

    pending: list[tuple[str, dict]] = []  # (컬렉션, 레코드)

    def flush():
        nonlocal pending
        by_col: dict[str, list[dict]] = {}
        for name, rec in pending:
            by_col.setdefault(name, []).append(rec)
        for name, recs in by_col.items():
            for i in range(0, len(recs), BATCH):
                part = recs[i : i + BATCH]
                vectors = embed([r["text"] for r in part])
                cols[name].upsert(
                    ids=[r["id"] for r in part], documents=[r["text"] for r in part],
                    metadatas=[r["meta"] for r in part], embeddings=vectors,
                )
                stats["embedded_chunks"] += len(part)
        pending = []

    for path, kind in files:
        try:
            doc_id, records, doc_hash = build_records(path, kind)
        except ValueError as e:
            stats["errors"].append(str(e))
            log(f"  ! 건너뜀: {e}")
            continue
        stats["docs"] += 1
        stats["chunks"] += len(records)
        stats["chars"] += sum(len(r["text"]) for r in records)
        if dry_run or not records:
            continue
        col = cols[kind]
        first = col.get(ids=[records[0]["id"]], include=["metadatas"])
        if first["ids"] and first["metadatas"][0].get("doc_hash") == doc_hash:
            stats["skipped_docs"] += 1
            continue
        col.delete(where={"doc_id": doc_id})  # 바뀐 문서의 옛 조각 제거
        pending += [(kind, r) for r in records]
        if len(pending) >= BATCH * 5:
            flush()
    if not dry_run:
        flush()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="컬렉션을 지우고 처음부터")
    ap.add_argument("--dry-run", action="store_true", help="임베딩 없이 조각 수만 센다")
    ap.add_argument("--only", choices=["companies", "reference"], help="한쪽만")
    args = ap.parse_args()
    print(f"임베딩: {provider_tag()}  / 저장소: {store.chroma_dir()}")
    s = run(reset=args.reset, dry_run=args.dry_run, only=args.only)
    print(
        f"문서 {s['docs']}건, 조각 {s['chunks']}개({s['chars']:,}자) — "
        + ("dry-run" if args.dry_run else f"새로 임베딩 {s['embedded_chunks']}개, 건너뜀 {s['skipped_docs']}건")
    )
    if s["errors"]:
        print(f"오류 {len(s['errors'])}건")
        sys.exit(1)


if __name__ == "__main__":
    main()
