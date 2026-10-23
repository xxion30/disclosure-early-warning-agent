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
    line((ROOT / "web" / "node_modules").exists() or None, "web/node_modules" + ("" if (ROOT / 'web' / 'node_modules').exists() else " 없음 (run_stack이 npm ci를 한다)"))

    print("\n[.env]")
    env_file = ROOT / ".env"
    line(env_file.exists(), ".env 파일", "`cp .env.example .env` 후 키를 채운다")
    try:
        from shared.config import get_settings

        s = get_settings()
    except Exception as e:  # noqa: BLE001
        line(False, f".env 읽기 실패: {type(e).__name__}")
        s = None
    if s:
        line(bool(s.OPENAI_API_KEY) if real else None, "OPENAI_API_KEY" + ("" if s.OPENAI_API_KEY else " 비어 있음"), "진짜 모드에 필요하다 (임베딩·모델)")
        line(None, "ANTHROPIC_API_KEY " + ("있음 → 기본 모델 Claude" if s.ANTHROPIC_API_KEY else "없음 → OpenAI가 기본·대체 모델을 모두 맡는다"))
        line(None, "LANGSMITH_API_KEY " + ("있음 → trace 전송" if s.LANGSMITH_API_KEY else "없음 → trace 없이 실행 (증빙 캡처는 못 한다)"))
        line(len(s.JWT_SECRET.encode()) >= 32 if real else None, "JWT_SECRET 길이(32바이트 이상)", "scripts.ops.prepare_release로 비밀값을 생성해 환경 변수로 전달한다.")
        line(None, f"GRAPH_ADAPTER={s.GRAPH_ADAPTER} (run_stack이 모드에 맞춰 덮어쓴다)")

    print("\n[데이터·실행 연결]")
    if s:
        line(bool(s.DART_API_KEY) if real else None, "DART_API_KEY 설정 여부", ".env에 키를 설정한다. 값은 출력하지 않는다.")
        try:
            from sqlalchemy import inspect, text
            from shared.db import get_engine
            engine = get_engine()
            tables = set(inspect(engine).get_table_names())
            required = {'companies', 'disclosures', 'pbs', 'analysis_jobs', 'organization_reviews'}
            line(required <= tables, "현재 DB의 서비스 테이블", "PostgreSQL: uv run alembic upgrade head / SQLite: run_stack 실행 시 스키마 준비")
            if required <= tables:
                with engine.connect() as connection:
                    users = connection.execute(text('SELECT count(*) FROM pbs')).scalar_one()
                    holdings = connection.execute(text('SELECT count(*) FROM holdings')).scalar_one()
                line(users > 0, f"로그인 계정 {users}개", "scripts.ops.bootstrap_admin으로 초기 계정 생성")
                line(holdings > 0, f"보유 내역 {holdings}개", "고객·보유 내역을 등록해야 종목 수집이 가능하다.")
        except Exception as exc:
            line(False, f"DB 연결 확인 실패 ({type(exc).__name__})", "DB_URL과 DB 서버 실행 상태를 확인한다. 기존 DB를 reset하지 않는다.")
        line(None, "실제 실행: hosted Graph + DB 작업 실행기 + DART 임시 색인 (run_stack에서 설정)")
        line(None, "키 유무만 검사했다. 실제 API 유효성 및 종단 분석 성공은 별도 검증한다.")

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
