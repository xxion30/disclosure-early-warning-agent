"""사업보고서 기본 점수(companies.base_score) 계산: DART 재무제표 수치 → CLAUDE.md 5-3장 규칙.

    uv run python scripts/compute_base_score.py            # 계산해서 보여주기만
    uv run python scripts/compute_base_score.py --write    # data/demo_companies.json에 base_score·base_reasons 기록
    uv run python scripts/seed_db.py --reset               # DB에 반영

기준 사업연도: 각 회사의 replay_window 시작일 **직전에 제출된** 사업보고서(= 그 시점에 이미 알려진 정보).
이후 보고서를 쓰면 사건을 미리 아는 셈(look-ahead)이라 쓰지 않는다. 점수는 rules.yaml의 base_score 표에서 읽는다.

계산하는 항목(재무제표 수치만으로 판단 가능): 영업손실 2년 연속 / 순이익 흑자+영업현금흐름 적자 / 영업이익 3년 연속 감소.
계산하지 않는 항목(감사보고서·주석 본문을 읽어야 함, TODO 확인 필요): 계속기업 불확실성 문단, 회계 관련 제재,
강조사항, 순이익 중 일회성 이익 30% 이상. 이 항목이 해당하면 실제 점수는 더 높을 수 있다.
SK는 D가 2025사업연도 보고서로 직접 확인한 값(2점)이 이미 있어 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.config import get_settings  # noqa: E402

BASE = "https://opendart.fss.or.kr/api"
COMPANIES = ROOT / "data" / "demo_companies.json"
RULES = ROOT / "mcp_servers" / "business" / "rules" / "rules.yaml"
TODO_ITEMS = "계속기업 불확실성·회계 제재·강조사항·일회성 이익은 감사보고서 본문 확인 필요(TODO)"


def reference_year(window_from: str) -> int:
    """replay 시작일 직전에 제출된 사업보고서의 사업연도 (사업보고서는 보통 3월 말까지 제출)."""
    d = date.fromisoformat(window_from)
    return d.year - 1 if (d.month, d.day) >= (3, 31) else d.year - 2


def dart(path: str, **params) -> dict:
    r = requests.get(f"{BASE}/{path}", params={"crtfc_key": get_settings().DART_API_KEY, **params}, timeout=30)
    r.raise_for_status()
    return r.json()


def annual_report_rcept(corp_code: str, year: int) -> tuple[str, str] | None:
    """(접수번호, 제출일) — 해당 사업연도의 최초 사업보고서."""
    res = dart("list.json", corp_code=corp_code, bgn_de=f"{year + 1}0101", end_de=f"{year + 1}1231",
               pblntf_detail_ty="A001", page_count=100)
    rows = [r for r in res.get("list", []) if r["report_nm"].startswith("사업보고서") and f"({year}.12)" in r["report_nm"]]
    rows.sort(key=lambda r: r["rcept_dt"])
    return (rows[0]["rcept_no"], rows[0]["rcept_dt"]) if rows else None


def statements(corp_code: str, year: int) -> dict[str, list[int | None]]:
    """{계정: [당기, 전기, 전전기]} — 연결(CFS) 우선, 없으면 별도(OFS)."""
    for fs in ("CFS", "OFS"):
        res = dart("fnlttSinglAcntAll.json", corp_code=corp_code, bsns_year=str(year), reprt_code="11011", fs_div=fs)
        if res.get("status") == "000":
            break
    else:
        raise RuntimeError(f"재무제표 없음: {corp_code} {year} ({res.get('message')})")

    def pick(sj, names):
        for row in res["list"]:
            if row["sj_div"] in sj and any(row["account_nm"].startswith(n) for n in names):
                vals = [row.get(k, "").replace(",", "") for k in ("thstrm_amount", "frmtrm_amount", "bfefrmtrm_amount")]
                return [int(v) if v.lstrip("-").isdigit() else None for v in vals]
        return [None, None, None]

    return {
        "fs": fs,
        "operating_income": pick(("IS", "CIS"), ("영업이익", "영업손실")),
        "net_income": pick(("IS", "CIS"), ("당기순이익", "당기순손실", "연결당기순이익")),
        "operating_cf": pick(("CF",), ("영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동 현금흐름")),
        "discontinued": pick(("IS", "CIS"), ("중단영업",)),
    }


def won(v: int | None) -> str:
    return "n/a" if v is None else f"{v:,}원"


def evaluate(st: dict, points: dict, year: int, rcept: tuple[str, str] | None, corp_name: str) -> tuple[int, list, list]:
    oi, ni, cf = st["operating_income"], st["net_income"], st["operating_cf"]
    ids = [("당기", year), ("전기", year - 1), ("전전기", year - 2)]
    series = lambda xs: ", ".join(f"{y}: {won(v)}" for (_, y), v in zip(ids, xs))  # noqa: E731
    rcept_no = rcept[0] if rcept else ""
    reasons, notes, score = [], [], 0

    def add(key: str, text: str, quote: str):
        nonlocal score
        score += points[key]
        item = {"item": key, "points": points[key], "reason": text}
        if rcept_no:  # 접수번호를 모르면 근거를 만들지 않는다(지어내지 않음)
            item["evidence"] = {
                "source": f"{corp_name} 사업보고서 {year}사업연도 재무제표({st['fs']})",
                "quote": quote, "rcept_no": rcept_no, "location": "DART fnlttSinglAcntAll",
            }
        reasons.append(item)

    if None not in oi[:2] and oi[0] < 0 and oi[1] < 0:
        add("operating_loss_2y", f"영업손실 2년 연속 ({year - 1}~{year})", f"영업이익(손실) {series(oi[:2])}")
    if ni[0] is not None and cf[0] is not None and ni[0] > 0 and cf[0] < 0:
        add("profit_but_negative_cfo", f"{year} 순이익 흑자 + 영업현금흐름 적자",
            f"당기순이익 {won(ni[0])}, 영업활동현금흐름 {won(cf[0])}")
    if None not in oi and oi[0] < oi[1] < oi[2]:
        add("operating_profit_decline_3y", f"영업이익 3년 연속 감소 ({year - 2}~{year})", f"영업이익(손실) {series(oi)}")
    notes.append(f"{year}사업연도 사업보고서 기준" + ("" if rcept else " (접수번호를 찾지 못해 근거 인용 없음)"))
    notes.append(TODO_ITEMS)
    return min(score, points["_max"]), reasons, notes


def enrich_confirmed(data: dict, points: dict) -> None:
    """D가 확정한 기본 점수(base_score_source가 있는 회사)의 사유에 DART 수치 근거(evidence)를 붙인다.

    점수는 바꾸지 않는다. 사유 문장의 키워드로 어떤 항목인지 찾고, DART 수치가 그 항목을 뒷받침할 때만 근거를 붙인다.
    근거가 없는 점수 항목은 AI 설명 단계에서 수동 확인으로 멈추기 때문에(A의 검증) 필요하다.
    """
    for c in data["companies"]:
        source = c.get("base_score_source") or ""
        exp = c.get("expected") or {}
        if not source or not exp.get("base_reasons") or c.get("base_reasons"):
            continue
        year = int(source[:4])
        rcept = annual_report_rcept(c["corp_code"], year)
        st = statements(c["corp_code"], year)
        oi, ni, dc = st["operating_income"], st["net_income"], st["discontinued"]
        series = ", ".join(f"{year - i}: {won(v)}" for i, v in enumerate(oi))
        reasons = []
        for text in exp["base_reasons"]:
            item = {"reason": text}
            quote = None
            if "3년 연속 감소" in text and None not in oi and oi[0] < oi[1] < oi[2]:
                item.update(item="operating_profit_decline_3y", points=points["operating_profit_decline_3y"])
                quote = f"영업이익(손실) {series}"
            elif "일회성" in text and ni[0] and dc[0] is not None and dc[0] / ni[0] >= 0.3:
                item.update(item="one_off_profit_30pct", points=points["one_off_profit_30pct"])
                quote = f"중단영업이익 {won(dc[0])} ÷ 당기순이익 {won(ni[0])} = {dc[0] / ni[0]:.1%}"
            if rcept and quote:
                item["evidence"] = {"source": f"{c['name']} 사업보고서 {year}사업연도 재무제표({st['fs']})", "quote": quote,
                                    "rcept_no": rcept[0], "location": "DART fnlttSinglAcntAll"}
            else:
                print(f"  ! {c['name']}: '{text[:30]}…' 근거를 DART 수치로 확인하지 못함 — 근거 없이 둠")
            reasons.append(item)
        c["base_reasons"], c["base_score"] = reasons, exp["base_score"]
        print(f"{c['name']}: 확정 기본 점수 {exp['base_score']}점의 사유 {len(reasons)}개에 DART 근거 {sum(1 for r in reasons if r.get('evidence'))}개 첨부")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="data/demo_companies.json에 기록")
    ap.add_argument("--enrich-confirmed", action="store_true", help="D가 확정한 값(SK)의 사유에 DART 수치 근거를 붙인다 (점수는 그대로)")
    args = ap.parse_args()
    cfg = yaml.safe_load(RULES.read_text(encoding="utf-8"))["base_score"]
    points = {**cfg["items"], "_max": cfg["max"]}
    data = json.loads(COMPANIES.read_text(encoding="utf-8"))
    if args.enrich_confirmed:
        enrich_confirmed(data, points)
        if args.write:
            COMPANIES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("기록: data/demo_companies.json")
        return
    for c in data["companies"]:
        if c.get("base_score_source") or (c.get("expected") or {}).get("base_score") is not None:
            print(f"{c['name']}: 이미 확정된 값이 있어 건너뜀")
            continue
        year = reference_year(c["replay_window"]["from"])
        rcept = annual_report_rcept(c["corp_code"], year)
        score, reasons, notes = evaluate(statements(c["corp_code"], year), points, year, rcept, c["name"])
        print(f"{c['name']:<10} {year}사업연도 → base_score {score}  (접수번호 {rcept[0] if rcept else '없음'})")
        for r in reasons:
            print(f"    +{r['points']} {r['reason']}\n       {r.get('evidence', {}).get('quote', '(근거 없음)')}")
        if args.write:
            c["base_score"], c["base_reasons"] = score, reasons
            c["base_score_source"] = f"{year}사업연도 사업보고서 재무제표(DART) · " + " · ".join(notes)
    if args.write:
        COMPANIES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("기록: data/demo_companies.json")


if __name__ == "__main__":
    main()
