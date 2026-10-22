"""데모 회사 5개를 진짜 모델로 한 번 재생해, 공시마다 AI가 찾은 신호(원문 인용 포함)를 eval/fixtures/signals.json에 저장한다.

저장한 신호로 `run_eval.py`의 조합 규칙 실험을 AI 호출 없이 몇 초 만에 되풀이한다 (규칙만 바꾸고 입력은 그대로).
격리된 임시 DB와 별도 포트(18100~18102)에서 돌리므로 팀 데이터(data/app.db)와 서버를 건드리지 않는다.
비용: 진짜 모델 호출이라 회사 5개 전체에 수백 원 안팎, 몇 분 걸린다.

    uv run python eval/capture_signals.py            # 5개 회사 전부 (--keep-going: 사건 전 공시를 끝까지)
    uv run python eval/capture_signals.py --stock 001570
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "demo"))
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

import verify_all as v  # noqa: E402  (서버 기동·정리 도우미 재사용)

OUT = ROOT / "eval" / "fixtures" / "signals.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", action="append")
    ap.add_argument("--timeout", type=float, default=240)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="eval_capture_"))
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "GRAPH_ADAPTER": "real", "QUEUE_ENABLED": "false",
           "DB_URL": f"sqlite:///{tmp / 'eval.db'}", "CHECKPOINT_DB": str(tmp / "ckpt.db"),
           "BUSINESS_MCP_URL": f"http://localhost:{v.PORTS['business']}/mcp",
           "RAG_MCP_URL": f"http://localhost:{v.PORTS['rag']}/mcp"}
    os.environ.update(env)
    import replay_all as ra  # noqa: E402
    import replay_demo as rd  # noqa: E402
    from sqlalchemy import select  # noqa: E402

    try:
        subprocess.run([v.PY, "scripts/data/seed_db.py", "--reset"], cwd=ROOT, env=env, capture_output=True, check=True)
        v.spawn([v.PY, "-m", "mcp_servers.business.server"], env, tmp / "b.log")
        v.spawn([v.PY, "-m", "mcp_servers.rag.server"], env, tmp / "r.log")
        v.spawn([v.PY, "-m", "uvicorn", "api.main:app", "--port", str(v.PORTS["api"])], env, tmp / "a.log")
        assert v.wait_port(v.PORTS["business"], "business") and v.wait_port(v.PORTS["rag"], "rag") and v.wait_port(v.PORTS["api"], "api")
        headers = rd.login(v.API, None, None)

        companies = json.loads((ROOT / "data" / "demo_companies.json").read_text(encoding="utf-8"))["companies"]
        stocks = args.stock or [c["stock_code"] for c in companies]
        from shared.db import get_session
        from shared.models import Company, RiskAssessment, Signal

        out: dict = {"_note": "eval/capture_signals.py가 진짜 모델 재생 결과에서 저장. run_eval.py의 combo 실험 입력.", "companies": {}}
        for stock in stocks:
            res = ra.run_company(v.API, headers, stock, args.timeout, keep_going=True)
            c = res["company"]
            with get_session() as s:
                co = s.scalars(select(Company).where(Company.stock_code == stock)).one()
                assessments = s.scalars(select(RiskAssessment).where(RiskAssessment.corp_code == co.corp_code)
                                        .order_by(RiskAssessment.assessment_id)).all()
                signals = s.scalars(select(Signal).where(Signal.corp_code == co.corp_code).order_by(Signal.filed_at, Signal.signal_id)).all()
                out["companies"][stock] = {
                    "name": c["name"], "corp_code": co.corp_code, "market": co.market,
                    "base_score": co.base_score, "base_reasons": co.base_reasons,
                    "event": c.get("event"),
                    "assessments": [{"rcept_no": a.rcept_no,
                                     "filed_at": next((x["filed_at"] for x in res["assessments"] if x["rcept_no"] == a.rcept_no), None),
                                     "level": a.level, "total_score": a.total_score, "combo_upgraded": a.combo_upgraded}
                                    for a in assessments],
                    "signals": [{"rcept_no": g.rcept_no, "signal_type": g.signal_type, "points": g.points,
                                 "detail": g.detail, "filed_at": g.filed_at} for g in signals],
                }
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")  # 회사마다 저장
            print(f"저장: {OUT.relative_to(ROOT)} ({len(out['companies'])}개 회사)", flush=True)
    finally:
        v.cleanup()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
