"""항목 API(주요사항보고서 주요정보) 값 → R4의 구조화 사실(`shared.facts.StructuredFacts` v1).

계약은 docs/contracts/collector.md 4-5(사실 모양)·3-3(정정 체인). 지키는 것:
- **의미가 확실한 필드만** 사실로 만든다 (값을 직접 확인한 항목 10종). 의미를 추측해야 하는 필드는 만들지 않는다.
- 값이 없으면 0·false로 채우지 않는다: `-`는 MISSING, 읽을 수 없는 값은 NEEDS_CHECK, 둘 다 사유를 적는다.
- 서술형 텍스트(사유·경위 등)가 200자를 넘으면 구조화하지 않고 NEEDS_CHECK로 표시한다 — 원문 검색(rag)으로 확인한다.
- 계산한 값(신주 비율 등)은 `raw`를 비우고 `source.field`에 계산식을 적는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

TEXT_LIMIT = 200

# 제목(공백 제거, 정정 접두 제거) → (위험 항목, 항목 API). 필드 의미를 확인한 10종만 둔다.
# 분할(cmpDvDecsn)·전환사채 외 사채(bdwtIsDecsn·exbdIsDecsn) 등은 값 확인 전이라 사실을 만들지 않는다 (structured_facts는 NULL).
ITEM_TITLES: dict[str, tuple[str, str]] = {
    "주요사항보고서(부도발생)": ("DEFAULT", "dfOcr"),
    "주요사항보고서(회생절차개시신청)": ("REHABILITATION", "ctrcvsBgrq"),
    "주요사항보고서(해산사유발생)": ("DISSOLUTION", "dsRsOcr"),
    "주요사항보고서(채권은행등의관리절차개시)": ("BANK_MANAGEMENT", "bnkMngtPcbg"),
    "주요사항보고서(영업정지)": ("BUSINESS_SUSPENSION", "bsnSp"),
    "주요사항보고서(유상증자결정)": ("RIGHTS_OFFERING", "piicDecsn"),
    "주요사항보고서(전환사채권발행결정)": ("CB_BW", "cvbdIsDecsn"),
    "주요사항보고서(감자결정)": ("CAPITAL_REDUCTION", "crDecsn"),
    "주요사항보고서(소송등의제기)": ("LAWSUIT", "lwstLg"),
    "주요사항보고서(회사합병결정)": ("MERGER", "cmpMgDecsn"),
}
PREFIX = re.compile(r"^\[[^\]]*\]")


@dataclass(frozen=True)
class Spec:
    field: str  # API 필드
    name: str  # 사실 이름 (영문 snake_case, 안정적)
    kind: str  # int | float | str | date
    unit: str | None = None


S = Spec
SPECS: dict[str, tuple[Spec, ...]] = {
    "dfOcr": (S("df_amt", "default_amount", "int", "원"), S("df_bnk", "default_bank", "str"), S("df_cn", "default_content", "str"),
              S("dfd", "default_date", "date"), S("df_rs", "default_reason", "str")),
    "ctrcvsBgrq": (S("apcnt", "applicant", "str"), S("cpct", "court", "str"), S("rqd", "request_date", "date"), S("rq_rs", "request_reason", "str")),
    "dsRsOcr": (S("ds_rs", "dissolution_reason", "str"), S("ds_rsd", "dissolution_date", "date")),
    "bnkMngtPcbg": (S("mngt_pcbg_dd", "management_start_date", "date"), S("mngt_int", "management_body", "str"),
                    S("mngt_pd", "management_period", "str"), S("mngt_rs", "management_reason", "str")),
    "bsnSp": (S("bsnsp_rm", "suspended_business", "str"), S("bsnsp_amt", "suspended_revenue", "int", "원"), S("rsl", "total_revenue", "int", "원"),
              S("sl_vs", "suspended_revenue_share_pct", "float", "%"), S("bsnsp_cn", "suspension_content", "str"), S("bsnspd", "suspension_date", "date")),
    "piicDecsn": (S("nstk_ostk_cnt", "new_common_shares", "int", "주"), S("nstk_estk_cnt", "new_other_shares", "int", "주"),
                  S("bfic_tisstk_ostk", "common_shares_before", "int", "주"), S("bfic_tisstk_estk", "other_shares_before", "int", "주"),
                  S("fv_ps", "par_value", "int", "원"), S("ic_mthn", "issue_method", "str"),
                  S("fdpp_fclt", "use_facility", "int", "원"), S("fdpp_bsninh", "use_business_acquisition", "int", "원"),
                  S("fdpp_op", "use_operating", "int", "원"), S("fdpp_dtrp", "use_debt_repayment", "int", "원"),
                  S("fdpp_ocsa", "use_securities_acquisition", "int", "원"), S("fdpp_etc", "use_other", "int", "원")),
    "cvbdIsDecsn": (S("bd_fta", "bond_face_amount", "int", "원"), S("bd_knd", "bond_kind", "str"), S("bdis_mthn", "issue_method", "str"),
                    S("bd_intr_ex", "coupon_rate_pct", "float", "%"), S("bd_intr_sf", "maturity_rate_pct", "float", "%"),
                    S("bd_mtd", "maturity_date", "date"), S("cv_prc", "conversion_price", "int", "원"),
                    S("cvisstk_cnt", "conversion_shares", "int", "주"), S("cvisstk_tisstk_vs", "conversion_shares_share_pct", "float", "%"),
                    S("cvrqpd_bgd", "conversion_start_date", "date"), S("cvrqpd_edd", "conversion_end_date", "date"),
                    S("act_mktprcfl_cvprc_lwtrsprc", "conversion_price_floor", "int", "원"), S("pymd", "payment_date", "date")),
    "crDecsn": (S("cr_rt_ostk", "reduction_ratio_common_pct", "float", "%"), S("cr_rt_estk", "reduction_ratio_other_pct", "float", "%"),
                S("crstk_ostk_cnt", "reduced_common_shares", "int", "주"), S("bfcr_cpt", "capital_before", "int", "원"),
                S("atcr_cpt", "capital_after", "int", "원"), S("bfcr_tisstk_ostk", "common_shares_before", "int", "주"),
                S("atcr_tisstk_ostk", "common_shares_after", "int", "주"), S("cr_std", "reduction_base_date", "date"),
                S("cr_mth", "reduction_method", "str"), S("cr_rs", "reduction_reason", "str")),
    "lwstLg": (S("icnm", "case_name", "str"), S("cpct", "court", "str"), S("ac_ap", "plaintiff", "str"), S("cfd", "filed_date", "date"),
               S("lgd", "litigation_date", "date")),
    "cmpMgDecsn": (S("mg_stn", "merger_type", "str"), S("mgptncmp_cmpnm", "counterparty", "str"), S("mgptncmp_rl_cmpn", "counterparty_relation", "str"),
                   S("mg_rt", "exchange_ratio", "str"), S("exevl_atn", "external_valuation", "str"),
                   S("rbsnfdtl_tast", "counterparty_total_assets", "int", "원"), S("rbsnfdtl_tdbt", "counterparty_total_debt", "int", "원"),
                   S("rbsnfdtl_teqt", "counterparty_total_equity", "int", "원"), S("rbsnfdtl_cpt", "counterparty_capital", "int", "원"),
                   S("rbsnfdtl_sl", "counterparty_revenue", "int", "원"), S("rbsnfdtl_nic", "counterparty_net_income", "int", "원"),
                   S("mgsc_mgdt", "merger_date", "date")),
}
# lwstLg에는 청구 금액 필드가 없다(확인됨). 자기자본 대비 청구액은 계산할 수 없다고 명시한다.
UNAVAILABLE_BY_API = {"lwstLg": ("claim_amount", "rq_cn", "청구 금액은 항목 API에 필드가 없다. 청구 취지(rq_cn)를 원문에서 확인해야 한다.")}

_DATE = re.compile(r"(\d{4})\s*[.년\-/]\s*(\d{1,2})\s*[.월\-/]\s*(\d{1,2})")
_ORIGINAL_DATE = re.compile(r"정정대상\s*공시서류의?\s*최초\s*제출일\s*:?\s*" + _DATE.pattern)


def split_title(title: str) -> tuple[bool, str]:
    """(정정 접두가 있는가, 접두와 공백을 뺀 제목). `[기재정정]`·`[첨부정정]` 같은 접두는 정정 체인의 구성원이다."""
    squeezed = re.sub(r"\s+", "", title or "")
    return bool(PREFIX.match(squeezed)), PREFIX.sub("", squeezed)


def item_for(title: str) -> tuple[str, str] | None:
    return ITEM_TITLES.get(split_title(title)[1])


def _iso(y: str, m: str, d: str) -> str | None:
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def parse_date(raw) -> str | None:
    m = _DATE.search(str(raw or ""))
    return _iso(*m.groups()) if m else None


def parse_original_date(text: str) -> str | None:
    """정정 공시 본문에서 `정정대상 공시서류의 최초제출일`을 읽는다 (YYYY-MM-DD). `2024년 09월 27일`·`2025.03.20`·`2022. 3. 3.` 모두."""
    m = _ORIGINAL_DATE.search(text or "")
    return _iso(*m.groups()) if m else None


def _num(raw, cast):
    """'5,917,159' → 5917159. 읽지 못하면 (None, 사유). 빈 값·'-'는 호출하는 make_fact가 먼저 MISSING으로 처리한다."""
    try:
        return cast(str(raw).replace(",", "").strip()), None
    except ValueError:
        return None, "not_numeric"


def _source(api: str, field: str, rcept_no: str, fetched_at: str) -> dict:
    return {"api": api, "field": field, "rcept_no": rcept_no, "fetched_at": fetched_at}


def _fact(name, dtype, raw, value, unit, status, reason, source) -> dict:
    return {"name": name, "dtype": dtype, "raw": raw, "value": value, "unit": unit, "status": status, "reason": reason, "source": source}


def make_fact(spec: Spec, raw, api: str, rcept_no: str, fetched_at: str) -> dict:
    src = _source(api, spec.field, rcept_no, fetched_at)
    dtype = {"int": "integer", "float": "number"}.get(spec.kind, "string")
    text = re.sub(r"\s+", " ", str(raw if raw is not None else "")).strip()
    if text in ("", "-"):
        return _fact(spec.name, dtype, text or None, None, spec.unit, "MISSING", "API가 값을 주지 않았다('-')", src)
    if spec.kind in ("int", "float"):
        value, problem = _num(text, int if spec.kind == "int" else float)
        if problem:
            return _fact(spec.name, dtype, text[:60], None, spec.unit, "NEEDS_CHECK", f"숫자로 읽지 못했다: {text[:40]}", src)
        return _fact(spec.name, dtype, text, value, spec.unit, "PRESENT", None, src)
    if spec.kind == "date":
        iso = parse_date(text)
        if iso is None:
            return _fact(spec.name, dtype, text[:60], None, spec.unit, "NEEDS_CHECK", f"날짜 형식을 읽지 못했다: {text[:40]}", src)
        return _fact(spec.name, dtype, text, iso, spec.unit, "PRESENT", None, src)
    if len(text) > TEXT_LIMIT:  # 긴 서술은 구조화하지 않는다 (원문 검색으로 확인)
        return _fact(spec.name, dtype, None, None, spec.unit, "NEEDS_CHECK", f"텍스트가 {TEXT_LIMIT}자를 넘어 구조화하지 않았다. 원문에서 확인한다", src)
    return _fact(spec.name, dtype, text, text, spec.unit, "PRESENT", None, src)


def _by_name(facts: list[dict]) -> dict[str, dict]:
    return {f["name"]: f for f in facts}


def _derived(api: str, facts: list[dict], rcept_no: str, fetched_at: str) -> list[dict]:
    """다른 사실에서 코드로 계산한 값. raw는 비우고 source.field에 계산식을 적는다."""
    by = _by_name(facts)
    ok = lambda n: by.get(n, {}).get("status") == "PRESENT"  # noqa: E731
    out: list[dict] = []
    if api == "piicDecsn":
        src = _source(api, "nstk_ostk_cnt ÷ bfic_tisstk_ostk × 100", rcept_no, fetched_at)
        if ok("new_common_shares") and ok("common_shares_before") and by["common_shares_before"]["value"] > 0:
            ratio = round(by["new_common_shares"]["value"] / by["common_shares_before"]["value"] * 100, 4)
            out.append(_fact("issue_ratio_common_pct", "number", None, ratio, "%", "PRESENT", None, src))
        else:
            out.append(_fact("issue_ratio_common_pct", "number", None, None, "%", "MISSING", "신주 수 또는 증자 전 주식 수가 없어 계산하지 못했다", src))
        src = _source(api, "ic_mthn에 '제3자배정' 포함 여부", rcept_no, fetched_at)
        if ok("issue_method"):
            out.append(_fact("third_party_allocation", "boolean", None, "제3자배정" in by["issue_method"]["value"], None, "PRESENT", None, src))
        else:
            out.append(_fact("third_party_allocation", "boolean", None, None, None, "MISSING", "증자 방식을 읽지 못했다", src))
    if api == "cvbdIsDecsn":
        src = _source(api, "act_mktprcfl_cvprc_lwtrsprc < cv_prc", rcept_no, fetched_at)
        if ok("conversion_price_floor") and ok("conversion_price"):
            out.append(_fact("refixing_floor_below_price", "boolean", None,
                             by["conversion_price_floor"]["value"] < by["conversion_price"]["value"], None, "PRESENT", None, src))
        else:
            out.append(_fact("refixing_floor_below_price", "boolean", None, None, None, "MISSING",
                             "전환가 조정 하한이 비어 있다. 조정 조항이 없는 것인지 적지 않은 것인지 API로는 알 수 없다", src))
    if api in UNAVAILABLE_BY_API:
        name, field, reason = UNAVAILABLE_BY_API[api]
        out.append(_fact(name, "integer", None, None, "원", "NEEDS_CHECK", reason, _source(api, field, rcept_no, fetched_at)))
    return out


def build_ambiguous_payload(api: str, row_count: int, *, rcept_no: str, corp_code: str, fetched_at: str) -> dict:
    """같은 접수번호에 항목 API 행이 여러 개일 때: 첫 행을 임의로 고르지 않고 모든 사실을 NEEDS_CHECK로 둔다.

    행을 구분할 안정된 값(대상·기간 등)을 아직 확인하지 못했으므로 `row_key`를 만들지 않는다. 원문 검색으로 확인한다.
    """
    reason = f"같은 접수번호에 항목 API 행이 {row_count}개라 첫 행을 고르지 않았다. 원문에서 확인한다"
    facts = []
    for spec in SPECS[api]:
        dtype = {"int": "integer", "float": "number"}.get(spec.kind, "string")
        facts.append(_fact(spec.name, dtype, None, None, spec.unit, "NEEDS_CHECK", reason, _source(api, spec.field, rcept_no, fetched_at)))
    return {"schema_version": "1", "rcept_no": rcept_no, "corp_code": corp_code, "collected_at": fetched_at, "facts": facts}


def build_payload(api: str, row: dict, *, rcept_no: str, corp_code: str, fetched_at: str) -> dict:
    """항목 API 행 1개 → StructuredFacts v1 payload. 응답에 없는 필드는 만들지 않는다."""
    facts = [make_fact(spec, row[spec.field], api, rcept_no, fetched_at) for spec in SPECS[api] if spec.field in row]
    facts += _derived(api, facts, rcept_no, fetched_at)
    return {"schema_version": "1", "rcept_no": rcept_no, "corp_code": corp_code, "collected_at": fetched_at, "facts": facts}
