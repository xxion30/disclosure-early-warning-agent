"""R5 평가 사례(eval/cases/r5_cases.json)를 실제 DART 값으로 만든다.

    uv run python scripts/demo/build_r5_cases.py            # 만들고 저장
    uv run python scripts/demo/build_r5_cases.py --dry-run  # 저장하지 않고 요약만

R3 사례(r3_cases.json)는 공시 한 건 단위다. R5는 겹치지 않게 두 가지를 맡는다.
- 회귀 타임라인: 이전 프로젝트 회사(금양·CJ CGV·SK)의 12개월 공시를 날짜순으로 — 첫 경보 시점·경보 빈도용
- 정상 회사: 위험 공시가 없는 대기업의 12개월 — 오탐용

지어내는 값은 없다. 접수번호·제목·접수일은 DART 공시 목록(list.json),
재무 지표는 R3 수집기(collector.indicators)를 DART 재무제표·감사의견 API에 그대로 돌린 값, 사건 공시의 값은 항목 API 원본이다.
DART_API_KEY가 필요하다 (.env). 읽기 전용 호출만 한다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from collector.dart_client import DartClient  # noqa: E402
from collector.indicators import compute_audit_opinion, compute_financial_indicators  # noqa: E402
from collector.reports import parse_periodic_title  # noqa: E402
from collector.watchlist import parse_corp_codes  # noqa: E402
from shared.rule_catalog import load_catalog  # noqa: E402

OUT = ROOT / "eval" / "cases" / "r5_cases.json"
AUDIT_API = "accnutAdtorNmNdAdtOpinion"
DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"

# 사건 공시 종류 → DART 주요사항보고서 항목 API (값을 원본 그대로 담는다)
ITEM_APIS = {
    "RIGHTS_OFFERING": ["piicDecsn"],
    "CB_BW": ["cvbdIsDecsn", "bdwtIsDecsn", "exbdIsDecsn"],
    "CAPITAL_REDUCTION": ["crDecsn"],
    "LAWSUIT": ["lwstLg"],
    "MERGER": ["cmpMgDecsn"],
    "TREASURY_DISPOSAL": ["tsstkDpDecsn"],
}

# 회귀 타임라인: 사건일(as_of) 전 12개월. 기대값은 R1 결정과 이전 프로젝트 기록에서 온다
TIMELINES = [
    {
        "case_id": "r5-timeline-001", "stock_code": "001570", "from": "2024-03-21", "as_of": "2025-03-21",
        "title": "금양 감사의견 거절까지 12개월 (2024-03-21 ~ 2025-03-21)",
        "expected_final_stage": "YELLOW",
        "expected_alert": True,
        "expectation_source": "R1 결정 (2026-10-23): 감사의견 거절(20250321802001) = 🟡 재무 경보. 이전 시나리오 A의 Critical은 폐기",
        "notes": "이전 프로젝트의 주인공 사례. 사건일보다 앞서 경보가 울렸는지(조기경보)는 정답이 아니라 측정값으로 본다.",
    },
    {
        "case_id": "r5-timeline-002", "stock_code": "079160", "from": "2022-06-20", "as_of": "2023-06-20",
        "title": "CJ CGV 대규모 유상증자까지 12개월 (2022-06-20 ~ 2023-06-20)",
        "expected_final_stage": None,
        "expected_alert": None,
        "expectation_source": "이전 프로젝트: '유상증자 공시 1건으로는 경보 없음'(옛 등급 기준). 새 규칙 기준은 R1 확인 필요",
        "ambiguity": "새 규칙은 유상증자 기본 2점 + 발행 비율 50% 이상 2점이다. 신주 74,700,000주 ÷ 증자 전 47,730,920주(약 156%)면 4점으로 🟠 기준(4점)에 닿는다. 옛 정답과 다를 수 있어 R1이 정해야 한다.",
        "notes": "옛 프로젝트의 '신호 하나로는 경보가 없어야 하는' 비교 사례.",
    },
    {
        "case_id": "r5-timeline-003", "stock_code": "034730", "from": "2025-04-02", "as_of": "2026-03-31",
        "title": "SK 12개월 (2025-04-02 ~ 2026-03-31)",
        "expected_final_stage": None,
        "expected_alert": False,
        "expectation_source": "이전 프로젝트: SK는 정상(Low) 사례. 설계서 STEP 06: 정상 사례는 경보가 울리지 않아야 한다",
        "notes": "기대값은 '공시가 없다'가 아니라 '경보가 없다'(단계 NONE 또는 WATCH).",
    },
]

# 정상 회사: 2024 사업보고서 접수일까지 12개월. 오탐(경보가 울리면 안 됨) 확인용
NORMALS = [
    {"case_id": "r5-normal-001", "stock_code": "005380"},  # 현대차
    {"case_id": "r5-normal-002", "stock_code": "035420"},  # NAVER
    {"case_id": "r5-normal-003", "stock_code": "066570"},  # LG전자
]


def classifier():
    catalog = load_catalog()
    entries, allowed = catalog["classification"], catalog["allowed"]

    def classify(title: str) -> str | None:
        t = "".join((title or "").split())
        if t.startswith("[첨부추가]"):
            return None
        return next((e["type"] for e in entries if any(w in t for w in e["keywords"])
                     or (e.get("all_keywords") and all(w in t for w in e["all_keywords"]))), None)

    return classify, allowed


def D(s: str) -> date:
    return date.fromisoformat(s)


def ymd(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def normalize_row(row: dict) -> dict:
    """list.json 행 또는 저장본 행 → {rcept_no, title, filed_at, url, is_correction}"""
    title = (row.get("report_nm") or row.get("title") or "").strip()
    rcept = row["rcept_no"]
    return {"rcept_no": rcept, "title": title, "filed_at": ymd(row.get("rcept_dt") or row.get("filed_at")),
            "url": row.get("url") or DART_URL.format(rcept), "is_correction": "정정]" in title.split(" ")[0] if title.startswith("[") else False}


def flatten(indicators: dict) -> dict:
    """IndicatorSet.indicators → R3 사례와 같은 모양 {지표: {status, ...값, notes?}}"""
    return {k: {"status": v.get("status"), **(v.get("detail") or {}), **({"notes": v["notes"]} if v.get("notes") else {})}
            for k, v in indicators.items()}


async def indicators_for(client: DartClient, corp_code: str, title: str) -> tuple[dict | None, dict | None]:
    """정기보고서 → R3 수집기와 같은 방식의 재무 지표 {type: {status, ...}} (연결 우선, 없으면 개별)"""
    report = parse_periodic_title(title)
    if report is None:
        return None, {"status": "SKIPPED", "reason": "12월 결산 정기보고서 제목이 아님"}
    facts, basis = {}, None
    for fs_div in ("CFS", "OFS"):
        rows = await client.financial_statements(corp_code, report.bsns_year, report.reprt_code, fs_div)
        if rows:
            res = compute_financial_indicators(rows, report.period_kind)
            facts.update(flatten(res.indicators))
            basis = fs_div
            break
    if report.reprt_code == "11011":
        audit = compute_audit_opinion(await client.periodic_report(AUDIT_API, corp_code, report.bsns_year, "11011"))
        facts.update(flatten(audit.indicators))
    meta = {"status": "PRESENT" if facts else "MISSING", "source": f"collector.indicators on OpenDART fnlttSinglAcntAll({basis or '없음'})"
            + (f"·{AUDIT_API}" if report.reprt_code == "11011" else ""), "bsns_year": report.bsns_year, "reprt_code": report.reprt_code}
    return (facts or None), meta


async def audit_for_report_filing(client: DartClient, corp_code: str, filed_at: str) -> tuple[dict | None, dict]:
    """감사보고서제출(거래소 공시) → 직전 사업연도 감사의견 (감사의견 API 값)"""
    year = int(filed_at[:4]) - 1
    audit = compute_audit_opinion(await client.periodic_report(AUDIT_API, corp_code, year, "11011"))
    facts = flatten(audit.indicators)
    return (facts or None), {"status": "PRESENT" if facts else "MISSING", "source": f"OpenDART {AUDIT_API} (사업연도 {year})", "bsns_year": year}


async def item_facts(client: DartClient, corp_code: str, dtype: str, rcept_no: str, frm: str, to: str) -> tuple[dict | None, dict | None]:
    for api in ITEM_APIS.get(dtype, []):
        rows = await client.major_report(api, corp_code, D(frm), D(to))
        hit = next((r for r in rows if r.get("rcept_no") == rcept_no), None)
        if hit:
            raw = {k: v for k, v in hit.items() if k not in ("rcept_no", "corp_cls", "corp_code", "corp_name")}
            return raw, {"status": "PRESENT", "api": api, "rcept_no": rcept_no}
    return None, {"status": "MISSING", "reason": "항목 API에서 같은 접수번호를 찾지 못함 (정정본만 주거나 항목 API가 없는 종류)"} if dtype in ITEM_APIS else None


async def build_steps(client, corp_code, rows, frm, to, classify, allowed):
    steps = []
    for row in sorted(rows, key=lambda r: (r["filed_at"], r["rcept_no"])):
        if not (frm <= row["filed_at"] <= to):
            continue
        dtype = classify(row["title"])
        if not dtype or not allowed.get(dtype):
            continue  # 위험 종류가 아닌 공시(지분 보고 등)는 판정에 넣지 않는다
        step = {**row, "disclosure_type": dtype}
        if dtype == "PERIODIC_REPORT":
            step["facts"], step["facts_meta"] = await indicators_for(client, corp_code, row["title"])
        elif dtype == "AUDIT_REPORT":
            step["facts"], step["facts_meta"] = await audit_for_report_filing(client, corp_code, row["filed_at"])
        elif dtype in ITEM_APIS:
            step["facts"], step["facts_meta"] = await item_facts(client, corp_code, dtype, row["rcept_no"], frm, to)
        steps.append(step)
    return steps


async def main(dry_run: bool) -> int:
    classify, allowed = classifier()
    cases = []
    async with DartClient() as client:
        corp_map = parse_corp_codes(await client.corp_code_zip())
        for spec in TIMELINES:
            corp_code, corp_name = corp_map[spec["stock_code"]]
            rows = [normalize_row(r) for r in await client.list_disclosures(corp_code, D(spec["from"]), D(spec["as_of"]))]
            steps = await build_steps(client, corp_code, rows, spec["from"], spec["as_of"], classify, allowed)
            cases.append({
                "case_id": spec["case_id"], "title": spec["title"], "group": "TIMELINE", "scope": "timeline",
                "stock_code": spec["stock_code"], "corp_code": corp_code, "corp_name": corp_name,
                "as_of": spec["as_of"], "window": {"from": spec["from"], "to": spec["as_of"]},
                "expected_final_stage": spec["expected_final_stage"], "expected_alert": spec["expected_alert"],
                "expectation_source": spec["expectation_source"], **({"ambiguity": spec["ambiguity"]} if spec.get("ambiguity") else {}),
                "window_disclosure_count": sum(spec["from"] <= r["filed_at"] <= spec["as_of"] for r in rows),
                "list_source": "OpenDART list.json",
                "steps": steps, "notes": spec["notes"],
            })
        for spec in NORMALS:
            corp_code, corp_name = corp_map[spec["stock_code"]]
            # 2024 사업보고서 접수일을 찾아 그날까지 12개월
            listing = [normalize_row(r) for r in await client.list_disclosures(corp_code, D("2025-01-01"), D("2025-06-30"))]
            report = next(r for r in listing if r["title"].startswith("사업보고서 (2024.12)"))
            to = report["filed_at"]
            frm = (date.fromisoformat(to) - timedelta(days=365)).isoformat()
            rows = [normalize_row(r) for r in await client.list_disclosures(corp_code, D(frm), D(to))]
            steps = await build_steps(client, corp_code, rows, frm, to, classify, allowed)
            cases.append({
                "case_id": spec["case_id"], "title": f"{corp_name} — 2024 사업보고서까지 12개월, 위험 공시 없음 확인", "group": "NORMAL",
                "scope": "company_year", "stock_code": spec["stock_code"], "corp_code": corp_code, "corp_name": corp_name,
                "as_of": to, "window": {"from": frm, "to": to}, "expected_final_stage": None, "expected_alert": False,
                "expectation_source": "설계서 STEP 06 (정상: 경보가 안 울려야 함)",
                "window_disclosure_count": len(rows), "list_source": "OpenDART list.json",
                "steps": steps,
                "notes": "기대값은 '공시가 없다'가 아니라 '경보가 없다'(단계 NONE 또는 WATCH). 대기업도 🟠 종류 공시(자기주식 처분·양수도 등)를 낼 수 있다.",
            })
    payload = {
        "_note": "R5가 만든 테스트 사례 (회귀 타임라인·정상 회사). 접수번호·제목·접수일은 DART 공시 목록의 실제 값, facts는 DART API 원본 또는 R3 수집기(collector.indicators)로 계산한 값이며 지어낸 값이 없다. "
                 "scripts/demo/build_r5_cases.py로 다시 만들 수 있고, scripts/demo/eval_cases.py로 채점한다. expected_*가 null이면 정답 미정(R1 확인 필요)이다.",
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "OpenDART (list.json, 항목 API, fnlttSinglAcntAll, accnutAdtorNmNdAdtOpinion)",
        "stages": ["RED", "ORANGE", "YELLOW", "COMPOSITE", "WATCH", "NONE"],
        "cases": cases,
    }
    for c in cases:
        print(f"{c['case_id']} {c['corp_name']} {c['window']['from']}~{c['window']['to']} 공시 {c['window_disclosure_count']}건 · 위험 종류 공시 {len(c['steps'])}건")
    if not dry_run:
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"저장: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    raise SystemExit(asyncio.run(main(ap.parse_args().dry_run)))
