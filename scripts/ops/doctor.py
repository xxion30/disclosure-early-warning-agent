"""내 노트북에서 돌릴 준비가 됐는지 점검한다. 고칠 것은 ✗ 줄 끝에 방법을 적어 준다.

    uv run python scripts/ops/doctor.py            # 진짜 모드 기준 (MCP 서버 + 진짜 모델)
    uv run python scripts/ops/doctor.py --fake     # 가짜 모드 기준 (키·MCP 서버 불필요)
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PORTS = {8000: "api", 8001: "business-mcp", 8002: "rag-mcp", 5173: "web"}
bad = 0


def line(ok: bool | None, text: str, fix: str = "") -> None:
    global bad
    mark = {True: "✓", False: "✗", None: "·"}[ok]
    if ok is False:
        bad += 1
    print(f" {mark} {text}" + (f"\n     → {fix}" if ok is False and fix else ""))


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fake", action="store_true", help="가짜 모드 기준으로만 점검")
    args = ap.parse_args()
    real = not args.fake

    print(f"[환경] {'진짜' if real else '가짜'} 모드 기준")
    line(sys.version_info[:2] == (3, 11), f"Python {sys.version.split()[0]} (3.11 필요)", "`uv sync`로 3.11 환경을 만든다")
    try:
        mcp_v = md.version("mcp")
        line(int(mcp_v.split(".")[0]) < 2, f"mcp {mcp_v} (2.x는 langchain-mcp-adapters와 충돌)", "`uv sync`로 uv.lock에 맞춘다")
    except md.PackageNotFoundError:
        line(False, "mcp 패키지 없음", "`uv sync`")
    node, npm = shutil.which("node"), shutil.which("npm")
    line(bool(node and npm), "Node.js / npm (화면용)" + ("" if node else " 없음"), "https://nodejs.org 에서 설치하거나 --no-web으로 화면 없이 돌린다")
    line((ROOT / "web" / "node_modules").exists() or None, "web/node_modules" + ("" if (ROOT / 'web' / 'node_modules').exists() else " 없음 (run_stack이 npm install을 한다)"))

    print("\n[.env]")
    env_file = ROOT / ".env"
    line(env_file.exists(), ".env 파일", "`cp .env.example .env` 후 키를 채운다")
    try:
        from shared.config import get_settings

        s = get_settings()
    except Exception as e:  # noqa: BLE001
        line(False, f".env 읽기 실패: {type(e).__name__}: {e}")
        s = None
    if s:
        line(bool(s.OPENAI_API_KEY) if real else None, "OPENAI_API_KEY" + ("" if s.OPENAI_API_KEY else " 비어 있음"), "진짜 모드에 필요하다 (임베딩·모델)")
        line(None, "ANTHROPIC_API_KEY " + ("있음 → 기본 모델 Claude" if s.ANTHROPIC_API_KEY else "없음 → OpenAI가 기본·대체 모델을 모두 맡는다"))
        line(None, "LANGSMITH_API_KEY " + ("있음 → trace 전송" if s.LANGSMITH_API_KEY else "없음 → trace 없이 실행 (증빙 캡처는 못 한다)"))
        line(s.JWT_SECRET != "change-me" or None, "JWT_SECRET " + ("설정됨" if s.JWT_SECRET != "change-me" else "기본값(change-me) — 개발용으로는 괜찮다"))
        line(None, f"GRAPH_ADAPTER={s.GRAPH_ADAPTER} (run_stack이 모드에 맞춰 덮어쓴다)")

    print("\n[데이터]")
    line((ROOT / "data" / "disclosures").glob("*.json").__next__() is not None if any((ROOT / "data" / "disclosures").glob("*.json")) else False,
         "data/disclosures/*.json (DART 공시 목록)", "git pull 후에도 없으면 D에게 확인 (scripts/data/fetch_dart.py)")
    docs = len(list((ROOT / "rag_docs").rglob("*.md")))
    line(docs > 0, f"rag_docs/ 원문 {docs}건", "git pull")
    db = ROOT / "data" / "app.db"
    if db.exists():
        try:
            c = sqlite3.connect(db)
            n = c.execute("select count(*) from companies").fetchone()[0]
            d = c.execute("select count(*) from disclosures").fetchone()[0]
            a = c.execute("select count(*) from risk_assessments").fetchone()[0]
            line(n > 0 and d > 0, f"data/app.db: 회사 {n}, 공시 {d}, 이미 처리한 평가 {a}", "`uv run python scripts/data/seed_db.py --reset`")
            if a:
                line(None, "이미 처리한 평가가 있다 → 같은 공시를 다시 실행하면 409. 처음부터 하려면 seed_db --reset 과 data/checkpoints.db 삭제")
        except sqlite3.Error as e:
            line(False, f"data/app.db 읽기 실패: {e}", "`uv run python scripts/data/seed_db.py --reset`")
    else:
        line(False, "data/app.db 없음", "`uv run python scripts/data/seed_db.py --reset`")
    if real:
        try:
            from mcp_servers.rag import store

            col = store.get_collection(store.DISCLOSURES)
            ref = store.get_collection(store.REFERENCE)
            line(col.count() > 0 and ref.count() > 0, f"data/chroma: 공시 조각 {col.count()}, 참고 문서 조각 {ref.count()}", "`uv run python -m mcp_servers.rag.ingest`")
        except Exception as e:  # noqa: BLE001
            line(False, f"data/chroma 색인 없음/불일치 ({type(e).__name__})", "`uv run python -m mcp_servers.rag.ingest` (설정을 바꿨다면 --reset)")

    print("\n[포트] (이미 쓰고 있으면 서버가 뜨지 않는다)")
    for port, name in PORTS.items():
        if port == 5173 and not (node and npm):
            continue
        line(port_free(port), f"{port} {name} 비어 있음", f"{port}번을 쓰는 프로그램을 끈다 (이미 서버가 떠 있다면 정상)")

    print()
    print("준비 완료 — `uv run python scripts/ops/run_stack.py" + ("" if real else " --fake") + "` 로 시작한다" if not bad else f"✗ {bad}개 고친 뒤 다시 실행한다")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
