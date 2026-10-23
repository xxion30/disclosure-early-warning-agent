"""🟡 재무 지표 계산: DART 재무제표·감사의견 행 → `financial_snapshots.indicators`에 넣을 값.

계약은 docs/contracts/collector.md 5장. 점수 구간은 R4의 rules.yaml이 적용하고, 여기서는 값과 상태만 만든다.
형식: `{지표명: {"status": "OK"|"MISSING", "detail": {...}, "notes": [...]}}`.

원칙
- 값을 못 구하면 0이 아니라 `MISSING`이다 (R2·R4 공통 요구). 이유는 `notes`에 남긴다.
- 어떤 계정을 읽었는지 `accounts`로 함께 돌려줘서 계산을 추적할 수 있게 한다 (financial_snapshots.sources용).
- 흐름(기간 값) 지표는 사업보고서(ANNUAL)에서만 만든다. 반기·분기 보고서의 금액 열이 누적인지 분기인지 확인하지 못했다(TODO 확인 필요).
- 계정 찾기는 `account_id`가 먼저, 없으면 `account_nm`이다 (표준계정코드가 없는 계정이 있다: 단기차입금 등).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

OK, MISSING = "OK", "MISSING"
ANNUAL = "ANNUAL"

# 지표 이름은 risk_engine/rules.yaml의 yellow 항목과 같다
CAPITAL_IMPAIRMENT = "CAPITAL_IMPAIRMENT"
OPERATING_CASH_FLOW = "OPERATING_CASH_FLOW"
INTEREST_BURDEN = "INTEREST_BURDEN"
SHORT_TERM_DEBT = "SHORT_TERM_DEBT"
AUDIT_OPINION = "AUDIT_OPINION"

BS, IS_CIS, CF = ("BS",), ("IS", "CIS"), ("CF",)

# 단기채무로 세는 계정 (R1 결정: CP·단기사채·1년 내 회사채(유동성사채 포함)·단기차입금·유동성장기부채).
# 아래 id·이름은 금양·위니아·오스템임플란트·CJ CGV·SK·삼성전자·SK하이닉스 7개 회사의 재무상태표에서 본 것이다.
DEBT_IDS = (
    "ifrs-full_ShorttermBorrowings",
    "ifrs-full_CurrentPortionOfLongtermBorrowings",
    "ifrs-full_CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued",
    "ifrs-full_CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
    "dart_CurrentPortionOfBonds",
    "dart_CurrentPortionOfConvertibleBonds",
)
DEBT_NAMES = (
    "단기차입금", "유동성장기차입금", "유동성장기부채", "유동성사채", "유동성전환사채", "유동성신주인수권부사채",
    "단기사채", "기업어음", "단기차입금및유동성장기부채",
)
# 단기차입금과 유동성장기부채를 한 줄로 합친 계정. 있으면 따로 세는 차입금 계정과 겹치므로 이 줄만 쓴다
COMBINED_IDS = ("ifrs-full_CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",)
COMBINED_NAMES = ("단기차입금및유동성장기부채",)
BORROWING_PARTS_IDS = ("ifrs-full_ShorttermBorrowings", "ifrs-full_CurrentPortionOfLongtermBorrowings")
BORROWING_PARTS_NAMES = ("단기차입금", "유동성장기차입금", "유동성장기부채")
DEBT_CANDIDATE = re.compile(r"사채|회사채|차입금|어음")

SEVERITY = {"적정": 0, "한정": 1, "부적정": 2, "의견거절": 3}


@dataclass
class IndicatorSet:
    indicators: dict = field(default_factory=dict)  # {지표명: {status, detail, notes}}
    accounts: dict = field(default_factory=dict)  # {지표명: [읽은 계정]} → sources


# ---- 계정 읽기 ----
def parse_amount(raw) -> int | None:
    """'1,234'·'-1234' → 정수. 비었거나 '-'이거나 읽을 수 없으면 None (0으로 바꾸지 않는다)."""
    if raw is None:
        return None
    text = str(raw).replace(",", "").strip()
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _norm(name) -> str:
    return re.sub(r"\s+", "", name or "")


def _find(rows: list[dict], sj: tuple, ids: tuple = (), names: tuple = ()) -> dict | None:
    """sj_div가 맞는 행 중 account_id가 목록에 있는 첫 행, 없으면 account_nm(공백 제거)이 목록에 있는 첫 행."""
    pool = [r for r in rows if r.get("sj_div") in sj]
    for r in pool:
        if r.get("account_id") in ids:
            return r
    for r in pool:
        if _norm(r.get("account_nm")) in names:
            return r
    return None


def _clearly_noncurrent(row: dict) -> bool:
    """id나 이름으로 비유동임이 분명한 계정 (notes에서 뺀다). 표준계정코드가 없고 이름만으론 모르는 계정은 False."""
    acc_id, name = row.get("account_id") or "", row.get("account_nm") or ""
    if re.search(r"Noncurrent|Longterm|LongTerm", acc_id) and "CurrentPortion" not in acc_id:
        return True
    return name.startswith(("비유동", "장기"))


def _ref(row: dict) -> dict:
    return {"sj_div": row.get("sj_div"), "account_id": row.get("account_id"), "account_nm": row.get("account_nm"),
            "thstrm_amount": row.get("thstrm_amount"), "frmtrm_amount": row.get("frmtrm_amount")}


def _ok(detail: dict, notes: list[str] | None = None) -> dict:
    return {"status": OK, "detail": detail, "notes": notes or []}


def _missing(reason: str, notes: list[str] | None = None) -> dict:
    return {"status": MISSING, "detail": {}, "notes": [reason, *(notes or [])]}


# ---- 지표 ----
def capital_impairment(rows: list[dict]) -> tuple[dict, list[dict]]:
    """자본잠식률 = (자본금 − 자기자본) ÷ 자본금 × 100. 자기자본(자본총계) ≤ 0이면 전액 잠식(100). (R1 결정)"""
    cap = _find(rows, BS, ("ifrs-full_IssuedCapital",), ("자본금",))
    eq = _find(rows, BS, ("ifrs-full_Equity",), ("자본총계",))
    used = [_ref(r) for r in (cap, eq) if r]
    equity = parse_amount(eq.get("thstrm_amount")) if eq else None
    if equity is None:
        return _missing("자본총계를 찾지 못했거나 읽지 못함"), used
    if equity <= 0:
        return _ok({"impairment_pct": 100.0, "total_equity": equity}), used
    capital = parse_amount(cap.get("thstrm_amount")) if cap else None
    if capital is None or capital <= 0:
        return _missing("자본금을 찾지 못했거나 0 이하라 나눌 수 없음"), used
    return _ok({"impairment_pct": max(0.0, (capital - equity) / capital * 100), "paid_in_capital": capital, "total_equity": equity}), used


def operating_cash_flow(rows: list[dict]) -> tuple[dict, list[dict]]:
    """negative_years: 당기 ≥ 0이면 0, 당기 < 0·전기 ≥ 0이면 1, 둘 다 < 0이면 2. 사업보고서의 당기·전기 열을 쓴다."""
    cfo = _find(rows, CF, ("ifrs-full_CashFlowsFromUsedInOperatingActivities",),
                ("영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동순현금흐름"))
    used = [_ref(cfo)] if cfo else []
    cur = parse_amount(cfo.get("thstrm_amount")) if cfo else None
    prev = parse_amount(cfo.get("frmtrm_amount")) if cfo else None
    if cur is None:
        return _missing("영업활동현금흐름 당기 값을 찾지 못함"), used
    if cur >= 0:
        return _ok({"negative_years": 0, "current": cur, "previous": prev}), used
    if prev is None:
        return _missing("당기가 적자인데 전기 값이 없어 1년 적자인지 2년 연속인지 알 수 없음"), used
    return _ok({"negative_years": 2 if prev < 0 else 1, "current": cur, "previous": prev}), used


def interest_burden(rows: list[dict]) -> tuple[dict, list[dict]]:
    """이자보상배율 = 영업이익 ÷ 금융비용(금융원가). 금융비용 ≤ 0이면 나눌 수 없어 MISSING (R1 결정, R4 확인 대기)."""
    op = _find(rows, IS_CIS, ("dart_OperatingIncomeLoss",), ("영업이익(손실)", "영업이익", "영업손실"))
    fin = _find(rows, IS_CIS, ("ifrs-full_FinanceCosts",), ("금융비용", "금융원가"))
    used = [_ref(r) for r in (op, fin) if r]
    op_v = parse_amount(op.get("thstrm_amount")) if op else None
    fin_v = parse_amount(fin.get("thstrm_amount")) if fin else None
    notes = ["금융비용은 이자 외 손익을 포함하므로 이자보상배율이 실제 이자 부담보다 낮게 나올 수 있다"]
    if op_v is None:
        return _missing("영업이익 계정을 찾지 못함"), used
    if fin_v is None:
        return _missing("금융비용·금융원가 계정을 찾지 못함"), used
    if fin_v <= 0:
        return _missing("금융비용이 0 이하라 나눌 수 없음"), used
    return _ok({"interest_coverage": op_v / fin_v, "operating_income": op_v, "finance_costs": fin_v}, notes), used


def short_term_debt(rows: list[dict]) -> tuple[dict, list[dict]]:
    """단기채무 ÷ 현금및현금성자산 × 100. 단기채무는 DEBT_IDS·DEBT_NAMES에 맞는 재무상태표 계정의 합이다.

    - 계정명이 회사마다 달라서 id·이름 목록에 맞는 것만 센다. 하나도 못 찾으면 '부채가 없다'와 '계정을 못 알아봤다'를
      구분할 수 없으므로 MISSING이다.
    - 유동/비유동을 알 수 없는 사채·회사채·차입금 계정은 세지 않고 notes에 남긴다 (예: 표준계정코드 없는 `회사채`).
    - 단기차입금과 유동성장기부채가 한 줄로 합쳐진 계정이 있으면 그 줄만 쓴다 (이중 계산 방지).
    """
    bs = [r for r in rows if r.get("sj_div") in BS]
    cash = _find(rows, BS, ("ifrs-full_CashAndCashEquivalents",), ("현금및현금성자산",))
    picked = [r for r in bs if r.get("account_id") in DEBT_IDS or _norm(r.get("account_nm")) in DEBT_NAMES]
    if any(r.get("account_id") in COMBINED_IDS or _norm(r.get("account_nm")) in COMBINED_NAMES for r in picked):
        picked = [r for r in picked
                  if not (r.get("account_id") in BORROWING_PARTS_IDS or _norm(r.get("account_nm")) in BORROWING_PARTS_NAMES)
                  or r.get("account_id") in COMBINED_IDS or _norm(r.get("account_nm")) in COMBINED_NAMES]
    seen, parts = set(), []
    for r in picked:  # 같은 계정이 두 번 나오면 한 번만
        key = (r.get("account_id"), _norm(r.get("account_nm")), r.get("thstrm_amount"))
        if key not in seen:
            seen.add(key)
            parts.append(r)
    ignored = [r.get("account_nm") for r in bs
               if r not in picked and DEBT_CANDIDATE.search(r.get("account_nm") or "")
               and not _clearly_noncurrent(r)]
    notes = [f"세지 않은 후보 계정(유동 여부 불명): {', '.join(dict.fromkeys(ignored))}"] if ignored else []
    used = [_ref(r) for r in [*parts, cash] if r]
    amounts = [parse_amount(r.get("thstrm_amount")) for r in parts]
    cash_v = parse_amount(cash.get("thstrm_amount")) if cash else None
    if not parts:
        return _missing("단기채무로 볼 계정을 하나도 찾지 못함", notes), used
    if any(a is None for a in amounts):
        return _missing("단기채무 계정 중 금액을 읽지 못한 것이 있음", notes), used
    if cash_v is None or cash_v <= 0:
        return _missing("현금및현금성자산을 찾지 못했거나 0 이하라 나눌 수 없음", notes), used
    total = sum(amounts)
    return _ok({"debt_to_cash_pct": total / cash_v * 100, "short_term_debt": total, "cash": cash_v,
                "components": [r.get("account_nm") for r in parts]}, notes), used


def compute_financial_indicators(rows: list[dict], period_kind: str = ANNUAL) -> IndicatorSet:
    """fnlttSinglAcntAll 행 → 지표. 사업보고서(ANNUAL)가 아니면 재무상태표 기반 지표만 만든다."""
    steps = [(CAPITAL_IMPAIRMENT, capital_impairment), (SHORT_TERM_DEBT, short_term_debt)]
    if period_kind == ANNUAL:
        steps += [(OPERATING_CASH_FLOW, operating_cash_flow), (INTEREST_BURDEN, interest_burden)]
    out = IndicatorSet()
    for name, fn in steps:
        out.indicators[name], out.accounts[name] = fn(rows)
    return out


# ---- 감사의견 ----
def normalize_opinion(raw) -> str | None:
    """'적정의견' → '적정', '거절' → '의견거절'. 모르는 문구는 None (추측하지 않는다)."""
    text = _norm(raw)
    if text == "거절":
        return "의견거절"
    text = text.removesuffix("의견") if text != "의견거절" else text
    return text if text in SEVERITY else None


def compute_audit_opinion(rows: list[dict]) -> IndicatorSet:
    """accnutAdtorNmNdAdtOpinion 행 → AUDIT_OPINION.

    한 보고서에 당기·전기·전전기가 같이 오고 `bsns_year`가 '제70기(당기)' 같은 문자열이라 '(당기)'로 당기 행을 고른다.
    같은 당기가 여러 행(중복)이고 의견이 갈리면 가장 나쁜 의견을 쓴다 (손실 방지 원칙).
    going_concern(계속기업 불확실성)은 텍스트 해석이라 채우지 않는다 (R2).
    """
    out = IndicatorSet()
    current = [r for r in rows if "(당기)" in _norm(r.get("bsns_year"))]
    refs = [{"api": "accnutAdtorNmNdAdtOpinion", "bsns_year": _norm(r.get("bsns_year")), "adt_opinion": r.get("adt_opinion"),
             "rcept_no": r.get("rcept_no")} for r in current]
    out.accounts[AUDIT_OPINION] = refs
    if not current:
        out.indicators[AUDIT_OPINION] = _missing("당기 감사의견 행을 찾지 못함")
        return out
    opinions = {o for o in (normalize_opinion(r.get("adt_opinion")) for r in current) if o}
    unknown = sorted({r.get("adt_opinion") or "" for r in current if normalize_opinion(r.get("adt_opinion")) is None})
    if not opinions:
        out.indicators[AUDIT_OPINION] = _missing("알 수 없는 감사의견 문구", [f"원문: {', '.join(unknown)}"])
        return out
    worst = max(opinions, key=SEVERITY.__getitem__)
    notes = []
    if len(opinions) > 1:
        notes.append(f"당기 의견이 갈려 가장 나쁜 의견을 썼다: {', '.join(sorted(opinions, key=SEVERITY.__getitem__))}")
    if unknown:
        notes.append(f"해석하지 못한 문구 무시: {', '.join(unknown)}")
    out.indicators[AUDIT_OPINION] = _ok({"opinion": worst}, notes)
    return out
