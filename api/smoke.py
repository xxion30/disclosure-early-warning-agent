"""시나리오 A 8~12단계를 실행 중인 API 서버에 대고 확인한다 (합치기·데모 전 점검용).

    uv run python -m api.smoke --rcept-no <감사의견 거절 공시 접수번호>
    uv run python -m api.smoke --rcept-no 20250401000001        # 가짜 Graph면 형식만 맞으면 됨

주의: 연락 업무를 실제로 만든다. seed_db.py로 새로 만든 DB에서 돌리고, 끝나면 다시 seed하세요.
기대값은 data/customers.json의 expected_impact_001570_critical(D의 평가 정답)에서 읽는다.
"""
import argparse
import json
import sys

import httpx

from shared.config import ROOT_DIR

EDITED = "{고객명}님, 감사의견 관련 공시 내용을 정리해 내일 오전에 직접 전화드리겠습니다."
PASSWORD = "demo1234"  # data/customers.json의 데모 PB 비밀번호


class Smoke:
    def __init__(self, base_url: str):
        self.c = httpx.Client(base_url=base_url, timeout=30)
        self.failed = 0

    def check(self, step: str, ok: bool, detail: str = "") -> bool:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {step}" + (f"  — {detail}" if detail and not ok else ""))
        self.failed += not ok
        return ok

    def login(self, email: str) -> dict:
        res = self.c.post("/auth/login", json={"email": email, "password": PASSWORD})
        res.raise_for_status()
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    def wait_events(self, rcept_no: str, h: dict) -> list[tuple[str, dict]]:
        """SSE를 끝(waiting_approval/done/failed)까지 읽는다."""
        events, name = [], None
        with self.c.stream("GET", f"/runs/{rcept_no}/events", headers=h, timeout=300) as res:
            res.raise_for_status()
            for line in res.iter_lines():
                if line.startswith("event: "):
                    name = line[7:]
                elif line.startswith("data: ") and name:
                    data = json.loads(line[6:])
                    events.append((name, data))
                    print(f"      · {name} {data.get('node', '')}")
        return events


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--rcept-no", required=True)
    args = ap.parse_args()

    seed = json.loads((ROOT_DIR / "data" / "customers.json").read_text(encoding="utf-8"))
    expected = seed["expected_impact_001570_critical"]
    owner = {c["customer_id"]: c["pb_id"] for c in seed["customers"]}
    targets = [e["customer_id"] for e in expected if e["priority"] in ("P1", "P2")]
    p01 = [c for c in targets if owner[c] == "P01"]
    p02 = [c for c in targets if owner[c] == "P02"]

    s = Smoke(args.base_url)
    h1, h2 = s.login("p01@demo.local"), s.login("p02@demo.local")
    print("[로그인] P01, P02 ✓")

    print(f"[실행] POST /runs {args.rcept_no}")
    res = s.c.post("/runs", json={"rcept_no": args.rcept_no}, headers=h1)
    if not s.check("202 Accepted", res.status_code == 202, res.text):
        return 1
    events = s.wait_events(args.rcept_no, h1)
    last_name, last = events[-1] if events else (None, {})
    if not s.check("8단계: pb_approval에서 멈춤 (waiting_approval)", last_name == "waiting_approval", str(last)):
        return 1
    aid = last.get("assessment_id")
    s.check("assessment_id가 이벤트에 있음", aid is not None)

    detail = s.c.get(f"/alerts/{aid}", headers=h1).json()
    s.check("5단계: 등급 Critical", detail.get("level") == "Critical", detail.get("level"))
    s.check("5단계: 원문 인용 1개 이상", len(detail.get("evidence", [])) >= 1)
    by_prio = {}
    for c in detail.get("customers", []):
        by_prio.setdefault(c["priority"], []).append(c["customer_id"])
    want = {}
    for e in expected:
        want.setdefault(e["priority"], []).append(e["customer_id"])
    s.check(f"6단계: 영향 고객 {len(expected)}명", len(detail.get("customers", [])) == len(expected),
            str(len(detail.get("customers", []))))
    s.check(f"6단계: P1 {sorted(want.get('P1', []))}", sorted(by_prio.get("P1", [])) == sorted(want.get("P1", [])),
            str(by_prio.get("P1")))
    s.check(f"6단계: P2 {sorted(want.get('P2', []))}", sorted(by_prio.get("P2", [])) == sorted(want.get("P2", [])),
            str(by_prio.get("P2")))

    drafts = s.c.get(f"/alerts/{aid}/drafts", headers=h1).json().get("drafts", [])
    s.check("7단계: P01 안내문에 {고객명} 자리표시", bool(drafts) and all("{고객명}" in d["draft"] for d in drafts))
    alert = next((a for a in s.c.get("/alerts", headers=h1).json() if a["assessment_id"] == aid), {})
    s.check("8단계: 경보 목록에 승인 대기", alert.get("status") == "WAITING_APPROVAL", alert.get("status"))

    print("[P01 결정]")
    res = s.c.post(f"/alerts/{aid}/decision", headers=h1,
                   json={"decisions": [{"customer_id": p02[0], "action": "approve"}]})
    s.check(f"12단계: P01은 P02 고객({p02[0]})을 승인할 수 없음 (403)", res.status_code == 403, str(res.status_code))
    decisions = [{"customer_id": p01[0], "action": "approve", "edited_draft": EDITED}]
    decisions += [{"customer_id": c, "action": "approve"} for c in p01[1:]]
    res = s.c.post(f"/alerts/{aid}/decision", json={"decisions": decisions}, headers=h1)
    s.check("9단계: P01 결정 접수", res.status_code == 202, res.text)
    s.wait_events(args.rcept_no, h1)
    mine = {t["customer_id"]: t for t in s.c.get("/tasks", headers=h1).json() if t["assessment_id"] == aid}
    s.check(f"10단계: 업무 {len(p01)}건 승인", sorted(mine) == sorted(p01) and all(t["status"] == "승인" for t in mine.values()),
            str(sorted(mine)))
    s.check(f"10단계: {p01[0]} 수정 문장 저장", mine.get(p01[0], {}).get("draft_message") == EDITED)

    print("[P02 결정]")
    reject, approve = p02[0], p02[1:]
    res = s.c.post(f"/alerts/{aid}/decision", headers=h2, json={"decisions": [
        {"customer_id": reject, "action": "reject", "reject_reason": "스모크 테스트 반려"},
        *({"customer_id": c, "action": "approve"} for c in approve),
    ]})
    s.check("11단계: P02 결정 접수", res.status_code == 202, res.text)
    events = s.wait_events(args.rcept_no, h2)
    s.check("모든 결정 후 done", bool(events) and events[-1][0] == "done", events[-1][0] if events else "")
    theirs = {t["customer_id"] for t in s.c.get("/tasks", headers=h2).json() if t["assessment_id"] == aid}
    s.check(f"12단계: 업무 {len(approve)}건 추가, 반려({reject})는 업무 없음", theirs == set(approve), str(sorted(theirs)))
    detail = s.c.get(f"/alerts/{aid}", headers=h2).json()
    s.check("12단계: 반려 기록", {c["customer_id"]: c["decision"] for c in detail["customers"]}.get(reject) == "반려")
    s.check("경보 상태 DONE", detail.get("status") == "DONE", detail.get("status"))

    print(f"\n결과: {'모두 통과' if not s.failed else f'{s.failed}개 실패'}")
    return 1 if s.failed else 0


if __name__ == "__main__":
    sys.exit(main())
