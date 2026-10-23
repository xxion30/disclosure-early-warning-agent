"""Offline regressions for real RAG metadata and invocation-owned cleanup."""
import asyncio
from dataclasses import replace
from hashlib import sha256

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.execution import Execution, current_execution, invoke_tool
from agent.retrieval import RAGScope, RAGContractError, current_rag_scope
from agent.runtime import AgentRuntime
from shared.schemas import ToolResponse
from test_contract_integration import setup, ORANGE


class Tools:
    def __init__(self, inner, change=None):
        self.inner, self.change, self.calls = inner, change, []

    async def call(self, name, args):
        self.calls.append((name, dict(args)))
        if name == 'release_disclosure_index':
            return ToolResponse(ok=True, data={'released_chunks': 0, 'deferred': True})
        result = await self.inner.call(name, args)
        if name == 'search_disclosure_text' and self.change:
            self.change(result.data)
        return result


@pytest.mark.parametrize('change,error', [
    (lambda d: d.update(truncated=True), '일부만 색인'),
    (lambda d: d.pop('normalization_version'), '정규화 버전'),
    (lambda d: d.update(truncated='true'), '완전성 표시'),
])
def test_incomplete_or_unversioned_search_stops_without_model_retry(change, error):
    w = setup()
    tools = Tools(w.deps.tools, change)
    deps = replace(w.deps, tools=tools)
    state = asyncio.run(AgentRuntime(deps, InMemorySaver()).start(ORANGE, context=w.ctx))
    assert state['status'] == 'MANUAL_CHECK' and error in state['error']
    searches = [a for n, a in tools.calls if n == 'search_disclosure_text']
    releases = [a for n, a in tools.calls if n == 'release_disclosure_index']
    assert len(searches) == len(releases) == 1
    assert searches[0]['holder'] == releases[0]['holder']
    assert not w.model_calls


def test_success_releases_same_holder_and_does_not_checkpoint_chunks():
    w = setup(); tools = Tools(w.deps.tools)
    deps = replace(w.deps, tools=tools)
    state = asyncio.run(AgentRuntime(deps, InMemorySaver()).start(ORANGE, context=w.ctx))
    assert state['status'] == 'DONE'
    assert {a['holder'] for n, a in tools.calls if n in ('search_disclosure_text', 'release_disclosure_index')}
    assert len({a['holder'] for n, a in tools.calls if n in ('search_disclosure_text', 'release_disclosure_index')}) == 1
    assert 'holder' not in str(state) and 'source_chunks' not in str(state)


def test_cancellation_after_search_releases_holder():
    async def run():
        w = setup(); tools = Tools(w.deps.tools)
        reached = asyncio.Event()
        class Model:
            async def generate(self, *args):
                reached.set()
                await asyncio.Future()
        rt = AgentRuntime(replace(w.deps, tools=tools, model=Model()), InMemorySaver())
        task = asyncio.create_task(rt.start(ORANGE, context=w.ctx))
        await asyncio.wait_for(reached.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert any(n == 'release_disclosure_index' for n, _ in tools.calls)
        assert current_rag_scope.get() is None
    asyncio.run(run())


def test_parallel_invocations_have_distinct_holders_for_same_run():
    w = setup()
    a = RAGScope.create(None, w.ctx.model_dump())
    b = RAGScope.create(None, w.ctx.model_dump())
    assert a.holder != b.holder and len(a.holder) <= 128
    with pytest.raises(RAGContractError, match='다른 실행'):
        a.arguments('release_disclosure_index', {'rcept_no': ORANGE, 'holder': b.holder})


@pytest.mark.parametrize('key,value', [('content_sha256', sha256(b'changed').hexdigest()),
                                      ('normalization_version', 'changed')])
def test_resume_detects_changed_document_or_normalization(key, value):
    w = setup(); digest = sha256(b'original').hexdigest()
    scope = RAGScope.create(None, w.ctx.model_dump(), [
        {'rcept_no': ORANGE, 'content_sha256': digest, 'normalization_version': 'v1'}])
    data = {'content_sha256': digest, 'normalization_version': 'v1', 'truncated': False}
    scope.validate(ORANGE, data)
    with pytest.raises(RAGContractError, match='명시적 재분석'):
        scope.validate(ORANGE, {**data, key: value})


def test_historical_nested_tool_call_also_receives_holder():
    async def run():
        w = setup(); tools = Tools(w.deps.tools)
        rag = RAGScope.create(tools, w.ctx.model_dump())
        async def nested():
            return await invoke_tool(tools, w.deps.harness, 'search_disclosure_text',
                                     {'rcept_no': ORANGE, 'query': '과거 근거', 'k': 5})
        a = current_execution.set(Execution(10, 8))
        b = current_rag_scope.set(rag)
        try:
            await nested()
            await rag.release()
        finally:
            current_rag_scope.reset(b); current_execution.reset(a)
        assert tools.calls[0][1]['holder'] == tools.calls[-1][1]['holder'] == rag.holder
    asyncio.run(run())


def test_optional_search_cannot_swallow_truncated_error():
    from agent.tool_loop import select_and_run
    async def run():
        w = setup()
        class Model:
            async def generate(self, prompt, schema, payload):
                return schema.model_validate({'requests': [
                    {'tool': 'search_disclosure_text', 'query': '조건 확인'}]})
        tools = Tools(w.deps.tools, lambda d: d.update(truncated=True))
        deps = replace(w.deps, tools=tools, model=Model())
        token = current_execution.set(Execution(10, 8))
        try:
            with pytest.raises(RAGContractError, match='일부만 색인'):
                await select_and_run(deps, 'extract_flags', ORANGE, {})
        finally:
            current_execution.reset(token)
    asyncio.run(run())


def test_real_r3_registry_keeps_other_r2_invocations_alive():
    from mcp_servers.rag.memory import Registry, Entry
    w = setup(); registry = Registry()
    a = RAGScope.create(None, w.ctx.model_dump())
    b = RAGScope.create(None, w.ctx.model_dump())
    def build():
        return Entry(key=ORANGE, texts=['contract fixture'], meta=[{}], vectors=[[1.0]])
    with registry.lease(ORANGE, build, a.holder):
        pass
    with registry.lease(ORANGE, build, b.holder):
        pass
    assert registry.release(ORANGE, a.holder)['deferred'] is True
    assert ORANGE in registry
    assert registry.release(ORANGE, b.holder)['released_chunks'] == 1
    assert ORANGE not in registry


def test_failed_release_is_bounded_and_does_not_overwrite_analysis(caplog):
    w = setup(); inner = w.deps.tools
    class FailingRelease(Tools):
        async def call(self, name, args):
            if name == 'release_disclosure_index':
                raise ConnectionError('sensitive details must not be logged')
            return await super().call(name, args)
    deps = replace(w.deps, tools=FailingRelease(inner))
    state = asyncio.run(AgentRuntime(deps, InMemorySaver()).start(ORANGE, context=w.ctx))
    assert state['status'] == 'DONE'
    assert 'TTL' in caplog.text and 'sensitive details' not in caplog.text
