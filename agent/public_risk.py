"""Allowlisted model/PB data. Never mutate or serialize private rule results.

This is an Agent boundary, not an HTTP authorization layer. R4 must use the
public projection and PB ownership checks in its API/SSE adapters as well.
"""
from agent.state import Evidence, APIEvidence
from shared.financial_evidence import FinancialEvidence
from harness.guardrails import find_score_disclosure


DISCLOSURE_FIELDS = ("rcept_no", "corp_code", "stock_code", "corp_name", "title", "filed_at")
LEVELS = frozenset({"Low", "Medium", "High", "Critical"})
STAGES = frozenset({"RED", "ORANGE", "YELLOW", "COMPOSITE", "WATCH", "NONE"})


def disclosure_context(disclosure):
    return {key: disclosure[key] for key in DISCLOSURE_FIELDS if key in disclosure}


def public_risk_context(result):
    """No raw note/detail, unknown keys, source_chunks or arithmetic inputs.

    Factual amounts/dates/ratios in verified quotes are retained. If an input
    quote itself contains a score expression, fail closed rather than edit a
    citation and pretend it is verbatim. Original evidence remains in State.
    """
    public = {"breakdown": []}
    if result.get("stage") in STAGES:
        public["stage"] = result["stage"]
    if result.get("level") in LEVELS:
        public["level"] = result["level"]
    for index, row in enumerate(result.get("breakdown", [])):
        label = str(row.get("item") or row.get("signal_type") or "확인 항목")
        if find_score_disclosure(label):
            label = "확인 항목"
        evidence = [(FinancialEvidence if e.get('kind') == 'financial' else APIEvidence if e.get('kind') == 'api' else Evidence).model_validate(e).model_dump()
                    for e in row.get("evidence", [])]
        if any(find_score_disclosure(str(e[k])) for e in evidence for k in e if e[k] is not None):
            raise ValueError("공개 근거의 내부 점수 표현 확인 필요")
        public["breakdown"].append({"breakdown_index": index, "item": label, "evidence": evidence})
    return public


def reference_context(data):
    """Reference documents are untrusted data and may contain old score tables.

    Drop score-bearing guide chunks from generation context. Do not alter the
    original RAG response, evidence or stored source.
    """
    chunks = data.get("chunks", []) if isinstance(data, dict) else []
    return {"chunks": [
        {k: row[k] for k in ("source", "text", "rcept_no", "location") if k in row}
        for row in chunks if isinstance(row, dict) and isinstance(row.get("text"), str)
        and not find_score_disclosure(row["text"])
    ][:5]}


def customer_context(customers):
    # No name, phone, account, investment profile or impact/priority fields.
    return [{"customer_id": c["customer_id"], "eval_amount": c.get("eval_amount")}
            for c in customers]
