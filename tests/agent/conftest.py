"""Synthetic fixtures only; these are not real DART disclosures or rule tests."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.ports import Dependencies
from shared.schemas import ToolResponse, ok

RCEPT = "TEST-DISCLOSURE-001"
EVIDENCE = {"source": "synthetic.md", "quote": "테스트 감사의견은 거절이다.",
            "rcept_no": RCEPT, "location": "chunk-1"}


class FakeTools:
    def __init__(self, level="Critical"):
        data = json.loads((Path(__file__).parents[2] / "data/customers.json").read_text())
        self.customers = [{k: row[k] for k in ("customer_id", "impact_score", "impact_level", "priority")}
                          | {"reasons": {}} for row in data["expected_impact_001570_critical"]]
        self.level = level
        self.calls = []
        self.error = None
        self.bad_chunks = False

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error:
            return ToolResponse.model_validate(self.error)
        responses = {
            "get_disclosure": {"rcept_no": RCEPT, "corp_code": "TEST-CORP", "title": "테스트 감사보고서",
                               "filed_at": "2025-04-01", "body_text": "원문 전체는 LLM 입력 금지"},
            "get_company_profile": {"stock_code": "001570", "name": "테스트 회사"},
            "get_signal_history": [],
            "search_disclosure_text": [{**EVIDENCE, "text": EVIDENCE["quote"]}],
            "calculate_risk_score": {"total_score": 2.5, "level": self.level, "combo_upgraded": False,
                "signals": [{"signal_type": "TEST", "points": 2.5, "detail": {}}],
                "breakdown": [{"signal_type": "TEST", "points": 2.5, "evidence": [EVIDENCE]}]},
            "compute_customer_impact": self.customers,
            "search_reference": {"chunks": [{"text": "상담 안내만 작성한다.", "source": "test-guide"}]},
        }
        value = copy.deepcopy(responses[name])
        if self.bad_chunks and name == "search_disclosure_text":
            value[0]["rcept_no"] = "OTHER"
        return ToolResponse.model_validate(ok(value))


class FakeModel:
    def __init__(self):
        self.calls = []
        self.bad_evidence = False
        self.unsafe = False
        self.invalid_drafts = False
        self.failure = None

    async def generate(self, prompt, schema, payload):
        self.calls.append((prompt, payload))
        if self.failure:
            raise self.failure
        if prompt == "extract_facts":
            evidence = {**EVIDENCE, "quote": "지어낸 인용"} if self.bad_evidence else EVIDENCE
            value = {"facts": [{"signal_type": "TEST", "values": {"opinion": "거절"}, "evidence": [evidence]}]}
        elif prompt == "explain_risk":
            value = {"items": [{"breakdown_index": 0, "explanation": "규칙 도구의 테스트 결과다.", "evidence": [EVIDENCE]}]}
        else:
            message = "{고객명}님, 매도하세요." if self.unsafe else "{고객명}님, 공시 내용을 확인하시고 PB에게 문의해 주세요."
            value = {"drafts": {c["customer_id"]: message for c in payload["customers"]}}
            if self.invalid_drafts:
                value["drafts"] = {}
        return schema.model_validate(value)


class FakeHarness:
    def __init__(self):
        self.events = []

    async def prepare_input(self, payload):
        return payload  # Fixtures contain only synthetic, already masked input.

    async def check_message(self, message):
        return ["매매 권유"] if "매도하세요" in message else []

    async def recover_llm(self, primary, fallback):
        for _ in range(3):
            try:
                return await primary()
            except Exception as exc:
                if getattr(exc, "status_code", None) not in (429, 503):
                    raise
        return await fallback()

    async def recover_tool(self, operation):
        response = await operation()
        if not response.ok and response.error.code == "TIMEOUT":
            return await operation()
        return response

    async def event(self, name, payload):
        self.events.append((name, payload))


class FakeStore:
    def __init__(self):
        self.saved = []
        self.audits = {}
        self.tasks = {}
        self.create_attempts = []
        self.fail_customer = None
        self.fail_after_write = False

    async def save(self, state, status):
        self.saved.append((copy.deepcopy(state), status))
        return 1

    async def audit(self, state, action, payload):
        key = payload.get("idempotency_key", str(len(self.audits)))
        self.audits[key] = (action, payload, state["trace_id"])

    async def create_task(self, payload):
        self.create_attempts.append(payload)
        if payload["customer_id"] == self.fail_customer:
            raise RuntimeError("test create failure")
        key = payload["idempotency_key"]
        self.tasks.setdefault(key, {**payload, "task_id": len(self.tasks) + 1})
        if self.fail_after_write:
            self.fail_after_write = False
            raise RuntimeError("crash after write")
        return self.tasks[key]["task_id"]


@pytest.fixture
def kit():
    tools, model, harness, store = FakeTools(), FakeModel(), FakeHarness(), FakeStore()
    deps = Dependencies(tools, model, harness, store,
                        classify=lambda title: "AUDIT" if "감사" in title else None,
                        signal_input=lambda extracted: [
                            {"signal_type": fact["signal_type"], "detail": fact["values"]}
                            for fact in extracted["facts"]],
                        create_task=store.create_task)
    return SimpleNamespace(deps=deps, tools=tools, model=model, harness=harness, store=store)
