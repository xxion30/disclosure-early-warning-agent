"""R2 flow tests using actual ZIP disclosure text and mocked R4 rule replies.

Mocked stages/scores are control-flow inputs, NOT financial labels or a measured
risk-prediction result. Existing customer records are the project's demo data.
"""
import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver

from agent.harness_llm import prepare_input
from agent.phase1 import FlagsOutput, Phase1Bindings
from agent.ports import Dependencies
from agent.rules import make_stage_classifier
from agent.runtime import AgentRuntime, open_runtime
from agent.state import ApprovalBatch, PBContext
from harness.guardrails import check_explanation, check_message
from shared.schemas import ToolResponse

ROOT = Path(__file__).resolve().parents[2]
RED = "20220103900001"      # 오스템임플란트 횡령·배임
ORANGE = "20230620000238"   # CJ CGV 유상증자
YELLOW = "20250321802001"   # 금양 감사보고서


def actual_disclosure(receipt):
    for file in (ROOT / "data/disclosures").glob("*.json"):
        for row in json.loads(file.read_text()):
            if row["rcept_no"] == receipt:
                for md in (ROOT / "rag_docs/companies").glob("*/*.md"):
                    text = md.read_text()
                    if f'rcept_no: "{receipt}"' in text[:400]:
                        _, front, body = text.split("---", 2)
                        meta = yaml.safe_load(front)
                        return row, meta, body.strip()
    raise AssertionError("ZIP의 실제 공시 사례가 필요합니다")


class MemoryStore:
    def __init__(self):
        self.saves, self.audits, self.tasks = [], {}, {}

    async def save(self, state, status):
        self.saves.append((copy.deepcopy(state), status))
        return 1

    async def audit(self, state, action, payload):
        self.audits[payload.get("idempotency_key", str(len(self.audits)))] = (action, payload)

    async def create_task(self, payload):
        key = payload["idempotency_key"]
        self.tasks.setdefault(key, {**payload, "task_id": len(self.tasks) + 1})
        return self.tasks[key]["task_id"]


class Harness:
    prepare_input = staticmethod(prepare_input)

    async def check_message(self, message):
        return check_message(message)

    async def recover_tool(self, op):
        result = await op()
        if not result.ok and result.error.code == "TIMEOUT":
            result = await op()
        return result

    async def recover_llm(self, primary, fallback):
        return await primary()

    async def event(self, name, payload):
        pass


def world(receipt=ORANGE, stage="ORANGE", score=0):
    row, meta, body = actual_disclosure(receipt)
    bucket = {RED: "RED", ORANGE: "ORANGE", YELLOW: "YELLOW"}[receipt]
    item = {RED: "횡령·배임", ORANGE: "유상증자", YELLOW: "감사의견"}[receipt]
    quote = "증자방식 주주배정후 실권주 일반공모" if receipt == ORANGE else body[:75]
    assert quote in body[:800]
    ev = {"source": f'{meta["corp_name"]} {meta["title"]}', "quote": quote,
          "rcept_no": receipt, "location": "chunk-0 (0-800자)"}
    chunk = {**ev, "text": body[:800]}
    seed = json.loads((ROOT / "data/seed/customers.json").read_text())
    owners = {c["customer_id"]: c["pb_id"] for c in seed["customers"]}
    holders = [{"customer_id": h["customer_id"], "eval_amount": h["eval_amount"]}
               for h in seed["holdings"] if h["stock_code"] == meta["stock_code"]]
    calls, model_calls = [], []
    options = {"bad_quote": False, "null_flag": False, "extra_flag": False,
               "unsafe_explanation": False, "unsafe_once": False,
               "missing_indicators": False, "rule_manual": False, "timeout_once": False}

    class Tools:
        async def call(self, name, args):
            calls.append((name, copy.deepcopy(args)))
            values = {
                "get_disclosure": row,
                "get_company_profile": {"stock_code": meta["stock_code"], "name": meta["corp_name"]},
                "search_disclosure_text": {"chunks": [chunk]},
                "search_reference": {"chunks": []},
                "get_holders": {"holders": holders},
            }
            return ToolResponse(ok=True, data=copy.deepcopy(values[name]))

    class Model:
        async def generate(self, prompt, schema, payload):
            model_calls.append((prompt, copy.deepcopy(payload)))
            if prompt == "select_tools":
                value = {"requests": []}
            elif prompt == "extract_flags":
                evidence = {**ev, "quote": "근거 없는 인용"} if options["bad_quote"] else ev
                flag = {"item": item, "flag": "증자방식", "value": None if options["null_flag"] else "주주배정후 실권주 일반공모", "evidence": [evidence]}
                value = {"flags": [flag]}
                if options["extra_flag"]:
                    value["flags"].append({**flag, "flag": "요청하지 않은 조건"})
            elif prompt == "explain_risk":
                attempt = sum(p == "explain_risk" for p, _ in model_calls)
                bad = options["unsafe_explanation"] or (options["unsafe_once"] and attempt == 1)
                value = {"items": [{"breakdown_index": i,
                          "explanation": "지금 매도하세요." if bad else "공시에서 확인한 사실에 따라 담당 PB의 검토가 필요합니다.",
                          "evidence": b["evidence"]} for i, b in enumerate(payload["score_result"]["breakdown"])]}
            else:
                value = {"drafts": {c["customer_id"]: "{고객명}님, 보유 종목의 공시가 접수되었습니다. 자세한 내용은 담당 PB에게 문의해 주세요."
                                    for c in payload["customers"]}}
            return schema.model_validate(value)

    async def rule(name, payload):
        calls.append((name, copy.deepcopy(payload)))
        if options["timeout_once"] and name == "score_orange":
            options["timeout_once"] = False
            raise TimeoutError("mock timeout")
        if name == "financial_snapshot":
            # The text value below comes from the actual document; the mock does
            # not compute a financial indicator or assess the issuer's risk.
            return {"indicators": {} if options["missing_indicators"] else {"audit_text": body[:75]}, "evidence": [ev]}
        if name != "judge_stage":
            return {"evidence": [ev]}
        return {"stage": stage, "orange_score": score if bucket == "ORANGE" else 0,
                "yellow_score": score if bucket == "YELLOW" else 0, "combined_ratio": 0,
                "breakdown": [] if stage == "NONE" else [
                    {"item": item, "evidence": [ev], "source_chunks": [chunk]}],
                "signals": [], "needs_manual_check": options["rule_manual"]}

    entries = [{"bucket": bucket, "item": item, "keywords": [meta["title"]],
                "text_flags": ["증자방식"] if bucket == "ORANGE" else []}]
    bindings = Phase1Bindings(classify=make_stage_classifier(entries), **{
        name: (lambda p, name=name: rule(name, p)) for name in (
            "mark_red", "score_orange", "financial_snapshot", "score_yellow", "judge_stage")})
    store = MemoryStore()
    deps = Dependencies(Tools(), Model(), Harness(), store, lambda _: None, lambda _: [], store.create_task, phase1=bindings)
    return SimpleNamespace(deps=deps, receipt=receipt, model_calls=model_calls, calls=calls,
                           store=store, options=options, owners=owners, holders=holders, ev=ev)


def run(w):
    return asyncio.run(AgentRuntime(w.deps, InMemorySaver()).start(w.receipt))


@pytest.mark.parametrize("stage", ["RED", "ORANGE", "YELLOW", "COMPOSITE", "WATCH", "NONE"])
def test_rule_stage_controls_routing_and_never_creates_unapproved_tasks(stage):
    w = world(stage=stage)
    state = run(w)
    assert state.get("error") is None, state
    assert state["stage"] == stage
    assert state["status"] == ("WAITING_APPROVAL" if stage in {"RED", "ORANGE", "YELLOW", "COMPOSITE"} else "DONE")
    assert not w.store.tasks
    assert "body_text" not in state["disclosure"]
    assert [c["eval_amount"] for c in state["affected_customers"]] == sorted(
        (c["eval_amount"] for c in state["affected_customers"]), reverse=True)


def test_red_does_not_ask_ai_to_extract_numeric_facts():
    w = world(RED, "RED")
    state = run(w)
    assert state["status"] == "WAITING_APPROVAL"
    assert "extract_flags" not in [p for p, _ in w.model_calls]
    assert "mark_red" in [p for p, _ in w.calls]
    assert not any(p in {"score_orange", "score_yellow", "financial_snapshot"} for p, _ in w.calls)


@pytest.mark.parametrize(("score", "stage", "status"), [(3, "WATCH", "DONE"), (4, "ORANGE", "WAITING_APPROVAL")])
def test_orange_boundary_is_supplied_by_rule_binding_not_recalculated(score, stage, status):
    w = world(stage=stage, score=score)
    state = run(w)
    assert state["orange_score"] == score and state["status"] == status
    payload = next(p for n, p in w.calls if n == "score_orange")
    assert payload["flags"][0]["value"] == "주주배정후 실권주 일반공모"
    assert "points" not in payload["flags"][0]


def test_yellow_uses_code_calculated_indicators_and_does_not_extract_flags():
    w = world(YELLOW, "YELLOW", score=5)
    state = run(w)
    assert state["status"] == "WAITING_APPROVAL" and state["yellow_score"] == 5
    names = [n for n, _ in w.calls]
    assert names.index("financial_snapshot") < names.index("score_yellow") < names.index("judge_stage")
    assert not any(n == "extract_flags" for n, _ in w.model_calls)


@pytest.mark.parametrize("problem", ["bad_quote", "null_flag", "extra_flag"])
def test_unverified_or_missing_flags_end_in_manual_check(problem):
    w = world()
    w.options[problem] = True
    state = run(w)
    assert state["status"] == "MANUAL_CHECK" and state["flag_attempts"] == 3
    assert "score_orange" not in [n for n, _ in w.calls] and not w.store.tasks


def test_explanation_is_checked_without_customer_name_placeholder():
    assert check_explanation("공시에서 확인된 사유를 검토할 필요가 있습니다.") == []
    assert check_message("공시에서 확인된 사유를 검토할 필요가 있습니다.")
    w = world()
    w.options["unsafe_once"] = True
    state = run(w)
    assert state["status"] == "WAITING_APPROVAL" and state["explanation_attempts"] == 2
    assert "매도하세요" not in state["ai_explanation"]


def test_explanation_rewrite_limit():
    w = world()
    w.options["unsafe_explanation"] = True
    state = run(w)
    assert state["status"] == "MANUAL_CHECK" and state["explanation_attempts"] == 3
    assert not state["drafts"] and not w.store.tasks


def test_missing_financial_input_and_rule_manual_request_stop():
    for problem in ("missing_indicators", "rule_manual"):
        w = world(YELLOW, "YELLOW")
        w.options[problem] = True
        assert run(w)["status"] == "MANUAL_CHECK"


def test_rule_timeout_is_retried_once():
    w = world()
    w.options["timeout_once"] = True
    state = run(w)
    assert state["status"] == "WAITING_APPROVAL"
    assert state["tool_call_counts"]["score_orange"] == 2


def test_stage_classifier_retains_multiple_routes_without_scoring():
    classify = make_stage_classifier([
        {"bucket": "YELLOW", "item": "감사의견", "keywords": ["감사보고서"]},
        {"bucket": "YELLOW", "item": "재무정보", "keywords": ["감사보고서"]},
    ])
    assert [r["item"] for r in classify("감사보고서제출")] == ["감사의견", "재무정보"]
    assert classify("주주총회소집공고") == []


def test_flags_schema_rejects_ai_scores_and_numeric_values():
    w = world()
    base = {"item": "유상증자", "flag": "증자방식", "value": True, "evidence": [w.ev]}
    with pytest.raises(ValueError):
        FlagsOutput.model_validate({"flags": [{**base, "points": 4}]})
    with pytest.raises(ValueError):
        FlagsOutput.model_validate({"flags": [{**base, "value": 4}]})


def test_approval_ownership_and_restart_preserve_contact_flow(tmp_path):
    w = world(YELLOW, "YELLOW", score=5)
    db = str(tmp_path / "phase1-checkpoints.db")

    async def scenario():
        by_pb = {pb: frozenset(c["customer_id"] for c in w.holders if w.owners[c["customer_id"]] == pb)
                 for pb in ("P01", "P02")}
        pb1, pb2 = PBContext(pb_id="P01", allowed_customer_ids=by_pb["P01"]), PBContext(pb_id="P02", allowed_customer_ids=by_pb["P02"])
        async with open_runtime(w.deps, db) as rt:
            first = await rt.start(w.receipt)
            trace = first["trace_id"]
            assert first["status"] == "WAITING_APPROVAL" and not w.store.tasks
            other = sorted(by_pb["P02"])[0]
            with pytest.raises(PermissionError):
                await rt.resume(w.receipt, ApprovalBatch(decisions=[{"customer_id": other, "status": "approved"}]), pb1)
            batch = ApprovalBatch(decisions=[{"customer_id": cid, "status": "approved"} for cid in sorted(by_pb["P01"])])
            mid = await rt.resume(w.receipt, batch, pb1)
            assert mid["status"] == "WAITING_APPROVAL"
            assert len(w.store.tasks) == len(by_pb["P01"])
        async with open_runtime(w.deps, db) as rt:
            assert (await rt.get_state(w.receipt))["trace_id"] == trace
            ids = sorted(by_pb["P02"])
            batch = ApprovalBatch(decisions=[{"customer_id": cid, "status": "rejected", "reject_reason": "담당 PB 개별 검토"}
                                             if i == 0 else {"customer_id": cid, "status": "approved"}
                                             for i, cid in enumerate(ids)])
            final = await rt.resume(w.receipt, batch, pb2)
            assert final["status"] == "DONE" and final["stage"] == "YELLOW"
            assert len(w.store.tasks) == len(w.holders) - 1
            assert all(t["customer_id"] != ids[0] for t in w.store.tasks.values())
            assert w.store.audits[f"1:{ids[0]}:decision"][1]["reject_reason"]
            view = await rt.get_review(w.receipt, pb1)
            assert set(view["drafts"]) == by_pb["P01"]
    asyncio.run(scenario())


def test_missing_postgres_dependency_is_explicit_not_sqlite_fallback(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres.aio", None)
    w = world()

    async def scenario():
        with pytest.raises(RuntimeError, match="R4"):
            async with open_runtime(w.deps, "postgresql://localhost/unconfigured"):
                pytest.fail("must not silently create a SQLite file")
    asyncio.run(scenario())
