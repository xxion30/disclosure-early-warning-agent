"""Regression tests for the review findings, separate from risk accuracy tests.

Disclosure text/IDs come from the PM ZIP; injected rule scores are control-flow
test inputs only. No model, DART or LangSmith network requests are made.
"""
import ast
import asyncio
import copy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.batch import analyze_receipts
from agent.catalog import SignalCatalog
from agent.evidence import validate_evidence
from agent.harness_llm import prepare_input
from agent.llm import StructuredLLM
from agent.model_adapter import KnownSignalsModel
from agent.phase1 import FlagsOutput, StageResult
from agent.ports import AgentError
from agent.public_risk import public_risk_context, reference_context
from agent.rules import make_classifier
from agent.runtime import AgentRuntime
from agent.state import ApprovalBatch, ExplanationOutput, FactsOutput, PBContext
from harness import context
from harness.guardrails import check_explanation, check_message, find_score_disclosure
from test_phase1 import ORANGE, ROOT, actual_disclosure, run, world


@pytest.mark.parametrize("text", [
    "위험 점수는 4점입니다.", "가중치 0.5를 반영했습니다.",
    "반영률은 50%입니다.", "총점 7로 분류했습니다.",
    "평가 결과는 ４점입니다.", "4\u200b점입니다.",
    "내부점수는 네 점입니다.", "orange_score=4", "risk_score:4", "4 points",
])
def test_score_expressions_are_rejected_in_explanations_and_messages(text):
    assert find_score_disclosure(text)
    assert check_explanation(text)
    assert check_message("{고객명}님, " + text)


def test_actual_document_amounts_and_dates_are_not_deleted():
    _, _, body = actual_disclosure(ORANGE)
    assert "74,700,000" in body and "1주당 액면가액 (원) 500" in body
    actual = "1주당 액면가액 (원) 500"
    assert actual in body
    assert check_explanation(actual) == []
    assert check_message("{고객명}님, " + actual) == []
    assert find_score_disclosure("2023-06-20, 발행주식 74,700,000주") == []
    assert find_score_disclosure("지분율 15%, 점유율 4.2%") == []


def test_allowlist_hides_nested_scores_and_notes_without_mutation():
    w = world()
    private = {"stage": "ORANGE", "orange_score": 4, "yellow_score": 0, "combined_ratio": 1,
               "secret_nested": {"total": 4}, "breakdown": [{"item": "유상증자", "points": 4,
               "weight": 0.5, "note": "0점 반영", "evidence": [w.ev],
               "source_chunks": [{"text": "4점"}], "extra": {"score": 4}}]}
    before = copy.deepcopy(private)
    public = public_risk_context(private)
    assert set(public) == {"stage", "breakdown"}
    assert set(public["breakdown"][0]) == {"breakdown_index", "item", "evidence"}
    assert not find_score_disclosure(json.dumps(public, ensure_ascii=False))
    assert private == before


def test_old_missing_explanation_fallback_does_not_disclose_rule_values():
    w = world()

    class Inner:
        async def generate(self, prompt, schema, payload):
            assert "points" not in json.dumps(payload)
            assert "0점" not in json.dumps(payload, ensure_ascii=False)
            return schema.model_validate({"items": [{"breakdown_index": 0,
                "explanation": "공시 근거 확인이 필요합니다.", "evidence": [w.ev]}]})

    breakdown = [{"item": "유상증자", "points": 4, "weight": 0.5, "note": "0점 적용", "evidence": [w.ev]}] * 2
    out = asyncio.run(KnownSignalsModel(Inner()).generate(
        "explain_risk", ExplanationOutput, {"score_result": {"breakdown": breakdown}}))
    assert len(out.items) == 2 and out.items[1].explanation.startswith("[규칙 요약]")
    assert all(not check_explanation(item.explanation) for item in out.items)


def test_model_receives_no_internal_scores_but_state_keeps_rule_result():
    w = world(score=4)
    state = run(w)
    assert state["orange_score"] == 4 and state["score_result"]["orange_score"] == 4
    for prompt, payload in w.model_calls:
        if prompt in {"explain_risk", "draft_message", "rewrite_message"}:
            assert set(payload["score_result"]) == {"stage", "breakdown"}
            serialized = json.dumps(payload, ensure_ascii=False)
            assert not any(key in serialized for key in ("orange_score", "combined_ratio", "source_chunks", '"points"', '"weight"'))


def patch_model(w, mutate):
    inner = w.deps.model

    class Model:
        async def generate(self, prompt, schema, payload):
            result = await inner.generate(prompt, schema, payload)
            data = result.model_dump()
            mutate(prompt, data)
            return schema.model_validate(data)

    w.deps = replace(w.deps, model=Model())


def test_leaking_explanation_is_retried_then_manual_check():
    w = world(score=4)

    def mutate(prompt, data):
        if prompt == "explain_risk":
            data["items"][0]["explanation"] = "내부 점수는 4점입니다."

    patch_model(w, mutate)
    state = run(w)
    assert state["status"] == "MANUAL_CHECK" and state["explanation_attempts"] == 3
    assert not state["drafts"] and not w.store.tasks


def test_leaking_draft_is_hidden_and_never_automatically_approved():
    w = world(score=4)

    def mutate(prompt, data):
        if prompt in {"draft_message", "rewrite_message"}:
            data["drafts"] = {cid: "{고객명}님, 위험점수 4점입니다." for cid in data["drafts"]}

    patch_model(w, mutate)
    state = run(w)
    assert state["status"] == "WAITING_APPROVAL"
    assert all(not text for text in state["drafts"].values())
    assert set(state["manual_drafts"]) == {c["customer_id"] for c in w.holders}
    assert not w.store.tasks
    for prompt, payload in w.model_calls:
        if prompt == "rewrite_message":
            assert all(not text for text in payload["previous_drafts"].values())


def test_pb_score_edit_does_not_consume_interrupt_or_create_task():
    w = world(score=4)
    cid = w.holders[0]["customer_id"]
    pb = PBContext(pb_id=w.owners[cid], allowed_customer_ids={cid})

    async def scenario():
        runtime = AgentRuntime(w.deps, InMemorySaver())
        before = await runtime.start(w.receipt)
        batch = ApprovalBatch(decisions=[{"customer_id": cid, "status": "approved",
                                         "edited_message": "{고객명}님, 위험 점수 4점입니다."}])
        with pytest.raises(ValueError, match="가드레일"):
            await runtime.resume(w.receipt, batch, pb)
        assert await runtime.get_state(w.receipt) == before
        assert not w.store.tasks
        valid = ApprovalBatch(decisions=[{"customer_id": cid, "status": "approved"}])
        after = await runtime.resume(w.receipt, valid, pb)
        assert len(after["customer_tasks"]) == 1
        review = await runtime.get_review(w.receipt, pb)
        assert "score_result" not in review and "orange_score" not in json.dumps(review)
        assert set(review["drafts"]) == {cid}
        assert set(review["decisions"][cid]) <= {"status", "pb_id", "decided_at"}

    asyncio.run(scenario())


def test_old_checkpoint_unsafe_text_is_not_reexposed():
    w = world()
    cid = w.holders[0]["customer_id"]

    async def scenario():
        runtime = AgentRuntime(w.deps, InMemorySaver())
        old = await runtime.start(w.receipt)
        old["ai_explanation"] = "총점 7입니다."
        old["drafts"][cid] = "{고객명}님, 7점입니다."
        async def snapshot(_):
            return old
        runtime.get_state = snapshot
        review = await runtime.get_review(w.receipt, PBContext(pb_id=w.owners[cid], allowed_customer_ids={cid}))
        assert review["drafts"][cid] == "" and cid in review["manual_drafts"]
        assert "7점" not in json.dumps(review, ensure_ascii=False)
        assert "총점" not in review["ai_explanation"]

    asyncio.run(scenario())


def test_score_bearing_reference_chunk_is_excluded_without_mutating_rag():
    original = {"chunks": [{"source": "old-guide", "text": "위험점수는 4점"},
                           {"source": "guide", "text": "공시 원문과 상담 내용을 안내한다."}]}
    result = reference_context(original)
    assert len(result["chunks"]) == 1 and result["chunks"][0]["source"] == "guide"
    assert len(original["chunks"]) == 2


def test_input_budget_keeps_citations_exact_and_original_unchanged():
    w = world()
    raw = {"evidence": [w.ev], "chunks": [{"text": "가" * 12000}] * 10}
    before = copy.deepcopy(raw)
    out = asyncio.run(prepare_input(raw))
    assert out["evidence"][0] == w.ev and raw == before
    assert context.estimate_tokens(json.dumps(out, ensure_ascii=False)) <= context.TOKEN_BUDGET


def test_unshrinkable_evidence_budget_fails_before_model():
    w = world()
    raw = {"evidence": [{**w.ev, "quote": "가" * (context.TOKEN_BUDGET + 1)}]}
    with pytest.raises(AgentError, match="예산 초과"):
        asyncio.run(prepare_input(raw))


def test_system_prompt_is_included_in_final_budget_check():
    class Model:
        def with_structured_output(self, schema):
            pytest.fail("provider must not be called for an oversized input")

    model = StructuredLLM(Model(), Model(), None)
    with pytest.raises(AgentError, match="지침을 포함한"):
        asyncio.run(model.generate("explain_risk", ExplanationOutput, {"text": "가" * context.TOKEN_BUDGET}))


@pytest.mark.parametrize("field", ["source", "rcept_no", "location", "quote"])
def test_citation_requires_every_source_coordinate(field):
    w = world()
    evidence = {**w.ev, field: "다른 출처"}
    chunk = {**w.ev, "text": w.ev["quote"]}
    with pytest.raises(AgentError):
        validate_evidence([evidence], [chunk])


def test_whitespace_citation_is_rejected():
    w = world()
    with pytest.raises(AgentError, match="빈 출처"):
        validate_evidence([{**w.ev, "quote": " "}], [{**w.ev, "text": " "}])


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), -1])
def test_invalid_or_missing_rule_scores_never_become_zero(value):
    data = {"stage": "NONE", "orange_score": value, "yellow_score": 0, "combined_ratio": 0}
    with pytest.raises(ValueError):
        StageResult.model_validate(data)
    with pytest.raises(ValueError):
        StageResult.model_validate({k: v for k, v in data.items() if k != "orange_score"})


@pytest.mark.parametrize("value", ["4", "50%", "", "  "])
def test_numeric_string_is_not_a_textual_flag(value):
    w = world()
    with pytest.raises(ValueError):
        FlagsOutput.model_validate({"flags": [{"item": "유상증자", "flag": "증자방식", "value": value, "evidence": [w.ev]}]})


def test_injected_catalog_never_reads_engine_files(monkeypatch):
    document = {"classification": [{"type": "RIGHTS", "keywords": ["유상증자"]}],
                "group_a": {}, "group_b": {"RIGHTS_OFFERING": {}},
                "allowed_signals_by_type": {"RIGHTS": ["RIGHTS_OFFERING"]}}
    catalog = SignalCatalog.from_document(document)
    monkeypatch.setattr("agent.rules.legacy_document", lambda *a: pytest.fail("must use injected mapping"))
    monkeypatch.setattr("agent.model_adapter.legacy_document", lambda *a: pytest.fail("must use injected catalog"))
    assert make_classifier(document=document)("유상증자결정") == "RIGHTS"
    w = world()

    class Inner:
        async def generate(self, prompt, schema, payload):
            return schema.model_validate({"facts": [{"signal_type": "RIGHTS_OFFERING", "values": {}, "evidence": [w.ev]}]})

    result = asyncio.run(KnownSignalsModel(Inner(), catalog=catalog).generate(
        "extract_facts", FactsOutput, {"disclosure_type": "RIGHTS", "chunks": [{**w.ev, "text": w.ev["quote"]}]}))
    assert len(result.facts) == 1
    with pytest.raises(TypeError):
        catalog.by_type["RIGHTS"] = frozenset()


def test_r2_does_not_import_forbidden_implementation_modules():
    for file in (ROOT / "agent").rglob("*.py"):
        if "adapters" in file.parts:
            continue  # R4-owned wiring is deliberately outside this patch.
        for node in ast.walk(ast.parse(file.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(name.split(".")[0] in {"risk_engine", "collector", "api"} for name in names), file


def test_batch_entry_deduplicates_and_returns_no_private_state():
    w = world(score=4)
    async def scenario():
        runtime = AgentRuntime(w.deps, InMemorySaver())
        rows = await analyze_receipts(runtime, [w.receipt, w.receipt])
        assert len(rows) == 1 and rows[0]["status"] == "WAITING_APPROVAL"
        assert set(rows[0]) == {"rcept_no", "stage", "status", "assessment_id"}
        assert len([c for c in w.calls if c[0] == "get_disclosure"]) == 1
    asyncio.run(scenario())


def test_guardrail_comparison_uses_identical_inputs_and_counts_score_leaks(monkeypatch):
    # R4's existing runner uses the top-level experiments package, not eval.*.
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from experiments.guardrail import compare_phase1
    rows = compare_phase1(["공시 근거 검토가 필요합니다.", "위험점수 4점입니다."],
                          {"C001": "{고객명}님, 위험점수 4점입니다."})
    assert rows[0]["통과 문안 중 점수 노출"] == 2
    assert rows[1]["통과 문안 중 점수 노출"] == 0
    assert rows[0]["설명문 수"] == rows[1]["설명문 수"] == 2


def test_model_evaluation_rejects_bad_quote_instead_of_counting_it_as_correct(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from experiments.model import compare_phase1
    w = world()
    payload = {"disclosure": {"rcept_no": w.receipt},
               "routes": [{"item": "유상증자", "text_flags": ["증자방식"]}],
               "chunks": [{**w.ev, "text": w.ev["quote"]}]}
    expected = {"item": "유상증자", "flag": "증자방식", "value": "주주배정후 실권주 일반공모"}
    class Model:
        def __init__(self, bad=False): self.bad = bad
        async def generate(self, prompt, schema, payload):
            ev = {**w.ev, "quote": "원문에 없는 인용"} if self.bad else w.ev
            return schema.model_validate({"flags": [{**expected, "evidence": [ev]}]})
    rows = asyncio.run(compare_phase1([{"payload": payload, "expected_flags": [expected]}],
                                     {"valid": Model(), "bad": Model(True)}))
    assert rows[0]["사람 검토 플래그 일치"] == 1
    assert rows[1]["근거 검증 통과"] == rows[1]["사람 검토 플래그 일치"] == 0


def test_capture_does_not_import_legacy_replay_or_overwrite_results(tmp_path):
    from eval.capture_signals import capture_phase1
    w = world()
    output = tmp_path / "new.json"
    async def scenario():
        runtime = AgentRuntime(w.deps, InMemorySaver())
        rows = await capture_phase1(runtime, [w.receipt, w.receipt], output)
        assert len(rows) == 1 and rows[0]["stage"] == "ORANGE"
        before = output.read_bytes()
        with pytest.raises(FileExistsError):
            await capture_phase1(runtime, [w.receipt], output)
        assert output.read_bytes() == before
    asyncio.run(scenario())
