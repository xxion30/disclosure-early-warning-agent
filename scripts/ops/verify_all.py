"""전체 검증을 한 번에: 테스트 → 시나리오 A → 안전장치 → (선택) 시나리오 B → 결과표.

    uv run python scripts/ops/verify_all.py                 # 테스트 + 시나리오 A + 안전장치 (약 10분, 모델 비용 소액)
    uv run python scripts/ops/verify_all.py --with-b        # + 위니아·오스템·CGV·SK (30분 이상)
    uv run python scripts/ops/verify_all.py --skip-tests    # pytest는 건너뜀
    uv run python scripts/ops/verify_all.py --ui            # 끝나면 같은 데이터로 화면을 띄워 눈으로 볼 수 있게 한다 (Ctrl+C로 종료)

**내 data/app.db와 켜져 있는 서버는 건드리지 않는다.** 임시 폴더의 DB와 별도 포트(API 18100, MCP 18101·18102)에서 돌린다.
진짜 모델·진짜 MCP 도구를 쓰므로 `.env`에 OPENAI_API_KEY가 있어야 한다. 결과는 ✓/✗로 출력하고 eval/results/verify_all.md에도 저장한다.
사람이 눈으로 볼 화면 항목은 마지막에 목록으로 알려 준다(--ui로 같은 데이터를 띄울 수 있다).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "demo"))
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

PY = sys.executable
PORTS = {"api": 18100, "business": 18101, "rag": 18102, "api503": 18103, "web": 5180}
API = f"http://localhost:{PORTS['api']}"
API503 = f"http://localhost:{PORTS['api503']}"
EVENT = "20250321802001"          # 금양 감사보고서제출(감사의견 거절)
CGV_EVENT = "20230620000238"      # CGV 유상증자결정 (503 시험용: 다른 공시와 독립이라 짧다)
KST = timezone(timedelta(hours=9))

results: list[tuple[str, bool | None, str]] = []   # (구역, 통과 여부(None=건너뜀), 설명)
procs: list[subprocess.Popen] = []
section = ""


def say(msg: str = "") -> None:
    print(msg, flush=True)


def head(title: str) -> None:
    global section
    section = title
    say(f"\n━━━ {title} ━━━")


def check(ok: bool | None, text: str) -> bool:
    results.append((section, ok, text))
    say(f"  {'✓' if ok else '–' if ok is None else '✗'} {text}")
    return bool(ok)


# ------------------------------------------------------------------ 프로세스
def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, name: str, timeout: float = 60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if port_open(port):
            return True
        time.sleep(0.5)
    check(False, f"{name}(:{port})가 {timeout:.0f}초 안에 뜨지 않음")
    return False


def spawn(cmd: list[str], env: dict, log: Path, cwd: Path = ROOT) -> subprocess.Popen:
    """별도 세션(프로세스 그룹)으로 띄운다. 끌 때 이 그룹만 종료하므로 다른 서버에 영향이 없다."""
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT, start_new_session=True)
    procs.append(p)
    return p


def stop(p: subprocess.Popen, wait: float = 8) -> None:
    if p.poll() is not None:
        return
    try:
        os.killpg(p.pid, signal.SIGINT)
        p.wait(timeout=wait)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


def cleanup() -> None:
    for p in procs:
        stop(p)


# ------------------------------------------------------------------ 도우미
def db_rows(db: Path, sql: str, args: tuple = ()) -> list[tuple]:
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def wait_until(fn, timeout: float = 60, every: float = 1.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def login(email: str, base: str = API) -> dict:
    r = requests.post(f"{base}/auth/login", json={"email": email, "password": "demo1234"}, timeout=30)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def decide(aid: int, headers: dict, decisions: list[dict], base: str = API) -> requests.Response:
    return requests.post(f"{base}/alerts/{aid}/decision", json={"decisions": decisions}, headers=headers, timeout=60)


# ------------------------------------------------------------------ 단계
def step_tests() -> None:
    head("1. 자동 테스트")
    out = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
    last = next((ln for ln in reversed(out.stdout.splitlines()) if "passed" in ln or "failed" in ln or "error" in ln), out.stdout[-200:])
    check(out.returncode == 0, f"pytest: {last.strip()}")


def step_env(tmp: Path, env: dict) -> bool:
    head("2. 환경과 임시 데이터")
    from shared.config import get_settings

    s = get_settings()
    if not check(bool(s.OPENAI_API_KEY), "OPENAI_API_KEY (진짜 모델·임베딩에 필요)"):
        return False
    r = subprocess.run([PY, "scripts/data/seed_db.py", "--reset"], cwd=ROOT, env=env, capture_output=True, text=True)
    check(r.returncode == 0 and "[공시] 594건" in r.stdout, "임시 DB 생성: 회사 5개·공시 594건 (내 data/app.db는 그대로)")
    try:
        from mcp_servers.rag import store

        n = store.get_collection(store.DISCLOSURES).count()
        ok = n > 0
    except Exception:  # noqa: BLE001
        ok, n = False, 0
    if not ok:
        say("  · 색인(data/chroma)이 없어 만듭니다 (약 20초)")
        ok = subprocess.run([PY, "-m", "mcp_servers.rag.ingest"], cwd=ROOT, env=env).returncode == 0
    check(ok, f"공시 원문 색인(data/chroma) {n or ''}조각".strip())
    return r.returncode == 0 and ok


def step_servers(tmp: Path, env: dict) -> bool:
    head("3. 서버 기동 (별도 포트)")
    spawn([PY, "-m", "mcp_servers.business.server"], env, tmp / "business.log")
    spawn([PY, "-m", "mcp_servers.rag.server"], env, tmp / "rag.log")
    ok = wait_port(PORTS["business"], "business-mcp") and wait_port(PORTS["rag"], "rag-mcp")
    spawn([PY, "-m", "uvicorn", "api.main:app", "--port", str(PORTS["api"])], env, tmp / "api.log")
    ok = ok and wait_port(PORTS["api"], "api")
    check(ok, f"business-mcp {PORTS['business']} · rag-mcp {PORTS['rag']} · api {PORTS['api']} 기동")
    return ok


def step_scenario_a(tmp: Path, env: dict) -> dict | None:
    head("4. 시나리오 A — 금양: 사건 전 경보 → 확정 악재 → 영향 고객")
    import replay_all as ra
    import replay_demo as rd

    headers = rd.login(API, None, None)
    res = ra.run_company(API, headers, "001570", 240, keep_going=False)
    for ok, text in ra.judge(res):
        check(ok, text)
    aid = (res.get("customers") or {}).get("assessment_id")
    if not aid:
        check(False, "사건 공시의 평가가 만들어지지 않음")
        return None
    d = requests.get(f"{API}/alerts/{aid}", headers=headers, timeout=30).json()
    check(d["status"] == "WAITING_APPROVAL", f"사건 평가 상태 WAITING_APPROVAL (실제 {d['status']})")
    check(any(b.get("evidence") for b in d["reasons"]), "점수 내역에 원문 인용(evidence)이 1개 이상 붙어 있음")
    check(len(d["customers"]) == 7 and not d.get("guardrail_flags"), f"영향 고객 {len(d['customers'])}명, 가드레일 flags {d.get('guardrail_flags')} (기대 7명, 없음)")
    drafts = requests.get(f"{API}/alerts/{aid}/drafts", headers=headers, timeout=30).json()
    texts = [x.get("draft") or x.get("text") or json.dumps(x, ensure_ascii=False) for x in (drafts.get("drafts") if isinstance(drafts, dict) else drafts) or []]
    check(bool(texts) and all("{고객명}" in t for t in texts), f"P01 안내문 초안 {len(texts)}건 모두 {{고객명}} 자리표시 유지 (실명은 모델에 가지 않음)")
    return {"aid": aid, "res": res}


def step_safety(tmp: Path, env: dict, ctx: dict) -> None:
    head("5. 안전장치 — 금지 표현 차단, 서버 재시작 후 이어서 실행")
    db = tmp / "verify.db"
    aid, customers = ctx["aid"], ctx["res"]["customers"]
    p01 = login("p01@demo.local")
    owner = dict(db_rows(db, "select customer_id,pb_id from customers"))
    targets = customers.get("P1", []) + customers.get("P2", [])
    mine = sorted(c for c in targets if owner[c] == "P01")
    theirs = sorted(c for c in targets if owner[c] == "P02")

    r = decide(aid, p01, [{"customer_id": mine[0], "action": "approve", "edited_draft": "{고객명}님, 지금 바로 매도하세요."}])
    check(r.status_code == 422, f"가드레일: 금지 표현('매도하세요')을 넣은 수정안은 거절됨 → HTTP {r.status_code} (기대 422)")
    check(not db_rows(db, "select 1 from contact_tasks"), "거절된 요청으로 연락 업무가 만들어지지 않음")

    # 서버를 껐다 켜도 같은 thread_id로 이어서 실행되는지: API만 재시작한다 (체크포인트는 임시 DB에 남아 있다)
    api = next(p for p in procs if p.args[:3] == [PY, "-m", "uvicorn"] and "18100" in p.args)
    stop(api)
    check(wait_until(lambda: not port_open(PORTS["api"]), 20) is not None, "API 서버를 종료함 (승인 대기 중인 경보가 있는 상태)")
    spawn([PY, "-m", "uvicorn", "api.main:app", "--port", str(PORTS["api"])], env, tmp / "api2.log")
    wait_port(PORTS["api"], "api(재시작)")
    p01 = login("p01@demo.local")
    d = requests.get(f"{API}/alerts/{aid}", headers=p01, timeout=30).json()
    check(d["status"] == "WAITING_APPROVAL" and d.get("graph_state_available") and len(d["customers"]) == 7,
          f"재시작 후에도 경보 상태·영향 고객 7명이 체크포인트에서 복원됨 (graph_state_available={d.get('graph_state_available')})")

    r = decide(aid, p01, [{"customer_id": theirs[0], "action": "approve"}])
    check(r.status_code == 403, f"권한: P01이 P02 담당 고객({theirs[0]})을 승인하면 거절됨 → HTTP {r.status_code} (기대 403)")

    dec = [{"customer_id": c, "action": "approve"} for c in mine]
    dec[0]["edited_draft"] = "{고객명}님, 금양 감사의견 관련 공시가 접수되어 안내드립니다. 상담이 필요하시면 연락 주세요."
    r = decide(aid, p01, dec)
    tasks = wait_until(lambda: len(db_rows(db, "select task_id from contact_tasks")) >= len(mine), 60)
    check(r.status_code == 202 and bool(tasks), f"재시작 후 P01 승인(1명은 문장 수정)이 이어서 실행되어 연락 업무 {len(mine)}건 생성 (기대 4건)")
    c1 = db_rows(db, "select draft_message from contact_tasks where customer_id=?", (mine[0],))
    check(bool(c1) and c1[0][0] == dec[0]["edited_draft"], f"{mine[0]}의 수정한 문장이 그대로 저장됨")
    status = db_rows(db, "select status from risk_assessments where assessment_id=?", (aid,))[0][0]
    check(status == "WAITING_APPROVAL", f"P02 결정 전에는 평가가 승인 대기로 남음 (실제 {status})")

    p02 = login("p02@demo.local")
    dec2 = [{"customer_id": c, "action": "reject", "reject_reason": "고객 요청으로 연락 보류"} if c == "C002" else {"customer_id": c, "action": "approve"} for c in theirs]
    r = decide(aid, p02, dec2)
    done = wait_until(lambda: db_rows(db, "select 1 from risk_assessments where assessment_id=? and status='DONE'", (aid,)), 60)
    n = len(db_rows(db, "select task_id from contact_tasks"))
    check(r.status_code == 202 and bool(done) and n == len(mine) + len(theirs) - 1, f"P02 결정(C002 반려) 후 평가 DONE, 연락 업무 총 {n}건 (기대 {len(mine) + len(theirs) - 1}건: 반려 고객은 업무 없음)")
    rej = db_rows(db, "select payload from audit_logs where action='decision.reject'")
    check(any("연락 보류" in json.loads(p[0]).get("reject_reason", "") for p in rej), "반려 사유가 audit_logs에 기록됨")
    acts = dict(db_rows(db, "select action,count(*) from audit_logs group by 1"))
    check(acts.get("decision.approve_edited", 0) >= 1 and acts.get("decision.reject", 0) == 1, f"audit_logs 결정 기록: {acts}")
    bad = db_rows(db, "select count(*) from risk_assessments where status in ('MANUAL_CHECK','FAILED')")[0][0]
    check(bad == 0, f"MANUAL_CHECK·FAILED 평가 {bad}건 (기대 0)")

    # 고객 현황: 내 고객만, 방금의 연락 이력이 보이고 다른 PB 고객은 안 보임
    cust = requests.get(f"{API}/customers", headers=p01, timeout=30).json()
    ids = {c["customer_id"] for c in cust["customers"]}
    first = next(c for c in cust["customers"] if c["customer_id"] == mine[0])
    check(ids.isdisjoint(theirs) and first["exposure"]["top_priority"] == "P1" and first["history"],
          f"고객 현황: 내 고객 {len(ids)}명만, {mine[0]}는 P1 노출이고 연락 이력 {len(first['history'])}건 표시")


def step_503(tmp: Path, env: dict) -> None:
    head("6. 장애 시나리오 — LLM 503 → 재시도 → 대체 모델")
    import replay_demo as rd

    db = tmp / "verify.db"
    before = dict(db_rows(db, "select action,count(*) from audit_logs where action like 'llm_%' group by 1"))
    spawn([PY, "-m", "uvicorn", "api.main:app", "--port", str(PORTS["api503"])], {**env, "FAKE_LLM_ERROR": "503"}, tmp / "api503.log")
    if not wait_port(PORTS["api503"], "api(FAKE_LLM_ERROR=503)"):
        return
    h = login("p01@demo.local", API503)
    code = requests.post(f"{API503}/runs", json={"rcept_no": CGV_EVENT}, headers=h, timeout=30).status_code
    a = rd.wait_for(API503, h, CGV_EVENT, 300)
    after = dict(db_rows(db, "select action,count(*) from audit_logs where action like 'llm_%' group by 1"))
    retries = after.get("llm_retry", 0) - before.get("llm_retry", 0)
    falls = after.get("llm_fallback", 0) - before.get("llm_fallback", 0)
    check(code == 202 and a is not None and a["status"] in ("DONE", "WAITING_APPROVAL"), f"503이 계속 나는 설정에서도 공시 처리가 완료됨 → 상태 {a and a['status']}")
    check(retries >= 2, f"기본 모델 호출이 재시도됨(1초·3초 간격): llm_retry {retries}건 기록 (기대 2건 이상)")
    check(falls >= 1, f"재시도 소진 후 대체 모델로 전환됨: llm_fallback {falls}건 기록 (기대 1건 이상)")


def step_langsmith(tmp: Path) -> None:
    head("7. 관측 — LangSmith trace")
    from shared.config import get_settings

    s = get_settings()
    if not (s.LANGSMITH_API_KEY and s.LANGSMITH_TRACING):
        check(None, "LANGSMITH_API_KEY가 없어 건너뜀 (발표 증빙 캡처는 키가 있어야 만들 수 있음)")
        return
    from langsmith import Client

    c = Client(api_key=s.LANGSMITH_API_KEY)

    def find():
        for r in c.list_runs(project_name=s.LANGSMITH_PROJECT, is_root=True, limit=100):
            if ((r.extra or {}).get("metadata") or {}).get("rcept_no") == EVENT:
                return r
        return None

    root = wait_until(find, 90, 5)
    if not check(root is not None, f"프로젝트 '{s.LANGSMITH_PROJECT}'에 금양 사건 공시의 trace가 올라옴"):
        return
    runs = list(c.list_runs(project_name=s.LANGSMITH_PROJECT, trace_id=root.trace_id, limit=100))
    names = {r.name for r in runs}
    need = {"receive", "extract_facts", "score_risk", "explain_risk", "find_customers", "draft_messages", "pb_approval"}
    check(need <= names, f"하나의 trace에 노드가 모여 있음 (누락: {sorted(need - names) or '없음'}), 실행 {len(runs)}개")
    llm = [r for r in runs if r.run_type == "llm"]
    check(len(llm) >= 3 and all((r.total_tokens or 0) > 0 for r in llm), f"LLM 호출 {len(llm)}회에 토큰·비용 기록 (비용 합계 ${sum(r.total_cost or 0 for r in llm):.4f})")


def step_b() -> None:
    head("8. 시나리오 B — 위니아·오스템·CGV·SK")
    import replay_all as ra
    import replay_demo as rd

    headers = rd.login(API, None, None)
    for stock in ("071460", "048260", "079160", "034730"):
        res = ra.run_company(API, headers, stock, 240, keep_going=False)
        say(f"  [{res['company']['name']}]")
        for ok, text in ra.judge(res):
            check(ok, f"{res['company']['name']}: {text}")


def step_ui(tmp: Path, env: dict) -> None:
    head("화면 확인 (사람이 눈으로)")
    web = ROOT / "web"
    if not (web / "node_modules").exists():
        subprocess.run(["npm", "install"], cwd=web, check=True)
    spawn(["npx", "vite", "--port", str(PORTS["web"]), "--strictPort"], {**env, "VITE_API_TARGET": API}, tmp / "web.log", cwd=web)
    if wait_port(PORTS["web"], "web", 40):
        say(f"\n  ▶ http://localhost:{PORTS['web']}  로그인 p01@demo.local / p02@demo.local, 비밀번호 demo1234")
        say("  이 화면은 방금 검증한 임시 데이터를 보여 줍니다(내 data/app.db와 별개). Ctrl+C로 종료합니다.")
        say("  눈으로 볼 것: 경보 목록의 '승인 대기'·등급 배지 / 경보 상세의 진행 단계·점수 내역 표·원문 근거 /")
        say("                안내문 검토 화면 / 고객 현황 탭(경보 노출·연락 이력) / 연락 업무 탭")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


def report(started: float) -> int:
    say("\n══════════ 결과 요약 ══════════")
    total = [r for r in results if r[1] is not None]
    bad = [r for r in total if not r[1]]
    secs: dict[str, list[tuple[bool | None, str]]] = {}
    for sec, ok, text in results:
        secs.setdefault(sec, []).append((ok, text))
    for sec, items in secs.items():
        passed = sum(1 for ok, _ in items if ok)
        counted = sum(1 for ok, _ in items if ok is not None)
        say(f"  {'✓' if passed == counted else '✗'} {sec}: {passed}/{counted}")
    say(f"\n  통과 {len(total) - len(bad)} / {len(total)}  ·  소요 {int(time.time() - started) // 60}분 {int(time.time() - started) % 60}초")
    if bad:
        say("\n  실패한 항목:")
        for sec, _, text in bad:
            say(f"    ✗ [{sec}] {text}")
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    lines = [f"# 전체 검증 결과", "", f"실행: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} · 커밋 {commit} · 진짜 모델·진짜 MCP 도구 · 임시 DB", ""]
    for sec, items in secs.items():
        lines += [f"## {sec}", ""] + [f"- {'✓' if ok else '–' if ok is None else '✗'} {text}" for ok, text in items] + [""]
    lines.append(f"**통과 {len(total) - len(bad)} / {len(total)}**")
    out = ROOT / "eval" / "results" / "verify_all.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"  저장: {out.relative_to(ROOT)}")
    return 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--with-b", action="store_true", help="시나리오 B(4개 회사)까지 (30분 이상)")
    ap.add_argument("--skip-tests", action="store_true", help="pytest 건너뜀")
    ap.add_argument("--ui", action="store_true", help="끝나면 같은 데이터로 화면을 띄운다 (5180)")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 지우지 않는다 (로그·DB 확인용)")
    args = ap.parse_args()

    busy = [f"{n}:{p}" for n, p in PORTS.items() if port_open(p) and (n != "web" or args.ui)]
    if busy:
        raise SystemExit(f"이미 쓰는 포트가 있어 시작하지 않습니다: {', '.join(busy)} (이전 검증이 남아 있다면 종료하세요)")

    tmp = Path(tempfile.mkdtemp(prefix="verify_all_"))
    env = {
        **os.environ, "PYTHONUNBUFFERED": "1", "GRAPH_ADAPTER": "real", "QUEUE_ENABLED": "false",
        "DB_URL": f"sqlite:///{tmp / 'verify.db'}", "CHECKPOINT_DB": str(tmp / "ckpt.db"),
        "BUSINESS_MCP_URL": f"http://localhost:{PORTS['business']}/mcp", "RAG_MCP_URL": f"http://localhost:{PORTS['rag']}/mcp",
    }
    os.environ.update(env)  # replay_*.py의 DB 조회도 같은 임시 DB를 보게 한다
    started = time.time()
    say(f"임시 폴더: {tmp}  (로그: business.log rag.log api.log api2.log api503.log)")
    code = 1
    try:
        if not args.skip_tests:
            step_tests()
        if step_env(tmp, env) and step_servers(tmp, env):
            ctx = step_scenario_a(tmp, env)
            if ctx:
                step_safety(tmp, env, ctx)
                step_503(tmp, env)
                step_langsmith(tmp)
            if args.with_b:
                step_b()
        code = report(started)
        if args.ui:
            step_ui(tmp, env)
    except KeyboardInterrupt:
        say("\n중단됨")
    finally:
        cleanup()
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            say(f"임시 폴더 보관: {tmp}")
    sys.exit(code)


if __name__ == "__main__":
    main()
