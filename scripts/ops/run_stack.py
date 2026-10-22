"""서버 전체를 한 명령으로 띄운다. Ctrl+C로 모두 함께 끈다.

    uv run python scripts/ops/run_stack.py            # 진짜 모드: business-mcp 8001 · rag-mcp 8002 · api 8000 · web 5173
    uv run python scripts/ops/run_stack.py --fake     # 가짜 모드: api 8000 · web 5173 (키·MCP 서버 불필요)
    uv run python scripts/ops/run_stack.py --no-web   # 화면 없이

처음 실행이면 필요한 준비(DB 만들기, 색인 만들기, npm install)를 먼저 한다. 점검은 `scripts/ops/doctor.py`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

COLORS = {"business": "36", "rag": "35", "api": "32", "web": "33"}
procs: list[tuple[str, subprocess.Popen]] = []


def say(msg: str) -> None:
    print(f"\033[1m[stack]\033[0m {msg}", flush=True)


def stream(name: str, proc: subprocess.Popen) -> None:
    color = COLORS.get(name, "0")
    for raw in iter(proc.stdout.readline, ""):
        print(f"\033[{color}m[{name:<8}]\033[0m {raw.rstrip()}", flush=True)


def start(name: str, cmd: list[str], env: dict, cwd: Path = ROOT) -> None:
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    procs.append((name, proc))
    threading.Thread(target=stream, args=(name, proc), daemon=True).start()


def wait_port(port: int, name: str, timeout: float = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for n, p in procs:
            if p.poll() is not None:
                say(f"{n}가 종료되었습니다 (코드 {p.returncode}). 위 로그를 확인하세요.")
                return False
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    say(f"{name}(:{port})가 {timeout:.0f}초 안에 뜨지 않았습니다.")
    return False


def stop_all() -> None:
    for name, p in procs:
        if p.poll() is None:
            p.terminate()
    deadline = time.time() + 5
    for name, p in procs:
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()


def prepare(real: bool, web: bool) -> bool:
    from shared.config import get_settings

    s = get_settings()
    if not (ROOT / ".env").exists():
        say("`.env`가 없습니다: `cp .env.example .env` 후 키를 채우세요.")
        return False
    db = ROOT / "data" / "app.db"
    if not db.exists():
        say("data/app.db가 없어 만듭니다 (seed_db.py)")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "data" / "seed_db.py"), "--reset"], cwd=ROOT, check=True)
    if real:
        if not s.OPENAI_API_KEY:
            say("진짜 모드에는 `.env`의 OPENAI_API_KEY가 필요합니다 (키 없이 화면만 볼 거면 --fake).")
            return False
        try:
            from mcp_servers.rag import store

            ok = store.get_collection(store.DISCLOSURES).count() > 0
        except RuntimeError as e:
            say(str(e))
            return False
        except Exception:  # noqa: BLE001 — 색인이 아직 없음
            ok = False
        if not ok:
            say("data/chroma 색인이 없어 만듭니다 (ingest, OpenAI 임베딩 약 20초)")
            subprocess.run([sys.executable, "-m", "mcp_servers.rag.ingest"], cwd=ROOT, check=True)
    if web and not (ROOT / "web" / "node_modules").exists():
        npm = shutil.which("npm")
        if not npm:
            say("npm이 없습니다. Node.js를 설치하거나 --no-web으로 실행하세요.")
            return False
        say("web/node_modules가 없어 npm install을 합니다")
        subprocess.run([npm, "install"], cwd=ROOT / "web", check=True)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fake", action="store_true", help="가짜 모드 (MCP 서버·키 없이 화면과 API만)")
    ap.add_argument("--no-web", action="store_true", help="화면(Vite)을 띄우지 않는다")
    args = ap.parse_args()
    real, web = not args.fake, not args.no_web

    def interrupt(*_):
        raise KeyboardInterrupt

    # 부모 셸이 SIGINT를 무시하게 해 둔 경우(백그라운드 실행 등)에도, 종료 신호를 받으면 자식 프로세스까지 함께 끈다.
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            signal.signal(sig, interrupt)
    if not prepare(real, web):
        sys.exit(1)

    env = {**os.environ, "PYTHONUNBUFFERED": "1", "GRAPH_ADAPTER": "real" if real else "fake"}
    if real:
        start("business", [sys.executable, "-m", "mcp_servers.business.server"], env)
        start("rag", [sys.executable, "-m", "mcp_servers.rag.server"], env)
        if not (wait_port(8001, "business-mcp") and wait_port(8002, "rag-mcp")):
            stop_all()
            sys.exit(1)
    start("api", [sys.executable, "-m", "uvicorn", "api.main:app", "--port", "8000"], env)
    if not wait_port(8000, "api"):
        stop_all()
        sys.exit(1)
    if web:
        npm = shutil.which("npm")
        start("web", [npm, "run", "dev"], env, cwd=ROOT / "web")
        wait_port(5173, "web", 30)

    say(f"준비됨 ({'진짜' if real else '가짜'} 모드). Ctrl+C로 모두 끕니다.")
    say("화면 http://localhost:5173 | API 문서 http://localhost:8000/docs | 로그인 p01@demo.local / p02@demo.local, 비밀번호 demo1234")
    web_restarts = 0
    try:
        while True:
            for name, p in list(procs):
                if p.poll() is None:
                    continue
                if name == "web" and web_restarts < 3:
                    # 화면 서버(Vite)는 보조라서, 죽어도 API·MCP는 그대로 두고 화면만 다시 띄운다
                    web_restarts += 1
                    say(f"web(화면 서버)가 종료되었습니다 (코드 {p.returncode}). API·MCP는 유지하고 화면만 다시 시작합니다 ({web_restarts}/3).")
                    procs.remove((name, p))
                    start("web", [shutil.which("npm"), "run", "dev"], env, cwd=ROOT / "web")
                    wait_port(5173, "web", 30)
                    continue
                say(f"{name}가 종료되었습니다 (코드 {p.returncode}). 전체를 끕니다.")
                raise KeyboardInterrupt
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        say("종료하는 중...")
        stop_all()


if __name__ == "__main__":
    main()
