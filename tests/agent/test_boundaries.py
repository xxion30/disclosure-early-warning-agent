import asyncio

import pytest
from langchain_core.messages import ToolMessage

from agent.llm import StructuredLLM, read_prompt
from agent.mcp_client import normalize_response, MCPTools
from agent.persistence import ToolAssessmentStore
from agent.state import DraftsOutput
from shared.schemas import ok


def test_mcp_normalization_and_tool_allowlist():
    assert normalize_response([{"type": "text", "text": '{"ok":true,"data":{"x":1}}'}]).data == {"x": 1}
    message = ToolMessage(content="not JSON", tool_call_id="test", artifact={"structured_content": ok({"x": 2})})
    assert normalize_response(message).data == {"x": 2}
    with pytest.raises(ValueError):
        normalize_response({"ok": False})
    with pytest.raises(ValueError):
        asyncio.run(MCPTools({}).call("update_task_status", {}))


@pytest.mark.parametrize("name", ["system", "extract_facts", "explain_risk", "draft_message", "rewrite_message"])
def test_versioned_prompts(name):
    version, body = read_prompt(name)
    assert version == "v1"
    assert body.strip()


def test_model_503_uses_d_recovery_and_same_schema(kit):
    class Model:
        model_name = "synthetic-model"
        def __init__(self):
            self.calls = 0
        def with_structured_output(self, schema):
            assert schema is DraftsOutput
            return self
        async def ainvoke(self, messages, config):
            self.calls += 1
            assert config["metadata"]["prompt_version"]["draft_message"] == "v1"
            return {"drafts": {"C001": "{고객명}님, 상담 부탁드립니다."}}

    async def scenario():
        primary, fallback = Model(), Model()
        model = StructuredLLM(primary, fallback, kit.harness, fake_error="503")
        result = await model.generate("draft_message", DraftsOutput, {})
        assert result.drafts["C001"].startswith("{고객명}")
        assert primary.calls == 0
        assert fallback.calls == 1
    asyncio.run(scenario())


def test_store_requires_explicit_upsert_confirmation(kit):
    with pytest.raises(ValueError):
        ToolAssessmentStore(kit.tools, kit.store.audit)


def test_classifier_reads_ordered_yaml(tmp_path):
    from agent.rules import load_classifier
    path = tmp_path / "rules.yaml"
    path.write_text('classification:\n  - type: CORRECTION\n    keywords: ["정정"]\n  - type: AUDIT\n    keywords: ["감사"]\n')
    classify = load_classifier(path)
    assert classify("정정 감사보고서") == "CORRECTION"
    assert classify("감사보고서") == "AUDIT"
    assert classify("일반 공시") is None


def test_unsafe_tool_message_cannot_be_success():
    message = ToolMessage(content='{"ok":true,"data":{}}', tool_call_id="test", status="error")
    with pytest.raises(ValueError):
        normalize_response(message)


def test_task_binding_checks_authority_and_shared_input(kit):
    from agent.persistence import ToolTaskCreator
    from agent.execution import Execution, current_execution
    from shared.schemas import ToolResponse

    class Tools:
        async def call(self, name, arguments):
            assert name == "create_contact_task"
            assert set(arguments) == {"assessment_id", "customer_id", "priority", "draft_message"}
            return ToolResponse.model_validate(ok({"task_id": 5}))

    async def scenario():
        async def allowed(pb, cid, assessment):
            return pb == "P01" and cid == "C001" and assessment == 1
        token = current_execution.set(Execution(120, 8))
        try:
            creator = ToolTaskCreator(Tools(), kit.harness, allowed, idempotency_confirmed=True)
            payload = {"pb_id": "P01", "customer_id": "C001", "assessment_id": 1,
                       "priority": "P1", "draft_message": "{고객명}님, 상담 부탁드립니다.",
                       "idempotency_key": "1:C001", "trace_id": "test"}
            assert await creator(payload) == 5
            with pytest.raises(PermissionError):
                await creator({**payload, "pb_id": "P02"})
        finally:
            current_execution.reset(token)
    asyncio.run(scenario())


def test_model_primary_retries_then_falls_back(kit):
    class Model:
        model_name = "synthetic"
        def __init__(self, fail=False):
            self.calls, self.fail = 0, fail
        def with_structured_output(self, schema):
            return self
        async def ainvoke(self, *args, **kwargs):
            self.calls += 1
            if self.fail:
                exc = RuntimeError("synthetic outage")
                exc.status_code = 503
                raise exc
            return {"drafts": {"C001": "{고객명}님, 상담 부탁드립니다."}}

    async def scenario():
        primary, fallback = Model(True), Model()
        await StructuredLLM(primary, fallback, kit.harness).generate("draft_message", DraftsOutput, {})
        assert primary.calls == 3
        assert fallback.calls == 1
    asyncio.run(scenario())
