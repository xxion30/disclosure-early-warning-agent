import asyncio
import pytest
from agent.adapters.checkpoints import open_checkpointer
from agent.adapters.executors import open_executors


def test_r4_injects_and_separates_checkpoint_threads(tmp_path):
    async def run():
        from langgraph.graph import StateGraph, START, END
        from typing import TypedDict
        class State(TypedDict):value:str
        builder=StateGraph(State);builder.add_node('write',lambda s:s);builder.add_edge(START,'write');builder.add_edge('write',END)
        async with open_checkpointer(tmp_path/'checkpoints.db') as saver:
            graph=builder.compile(checkpointer=saver)
            for thread,value in [('public:fixture','public'),('review:org-a','a'),('review:org-b','b')]:
                await graph.ainvoke({'value':value},config={'configurable':{'thread_id':thread}})
        async with open_checkpointer(tmp_path/'checkpoints.db') as saver:
            graph=builder.compile(checkpointer=saver)
            for thread,value in [('public:fixture','public'),('review:org-a','a'),('review:org-b','b')]:
                state=await graph.aget_state({'configurable':{'thread_id':thread}})
                assert state.values['value']==value
    asyncio.run(run())


def test_rcept_only_runtime_is_not_used_as_multitenant_fallback(tmp_path):
    class Legacy:
        async def start(self,rcept_no):return {}
    async def run():
        async with open_executors(lambda deps,saver:Legacy(),None,tmp_path/'legacy.db'):
            pytest.fail('legacy runtime must be rejected')
    with pytest.raises(RuntimeError,match='진입점'):asyncio.run(run())


def test_factory_injects_initialized_saver_into_real_r2_runtime_and_closes_it(tmp_path):
    from types import SimpleNamespace
    from agent.integration import ExecutionContext
    from agent.runtime import AgentRuntime

    async def run():
        deps = SimpleNamespace(unified=object(), phase1=None)
        captured = {}
        receipt = '20250321802001'
        context = ExecutionContext(assessment_key='checkpoint-contract', as_of='2025-03-21',
                                   rules_version='rules-contract', input_version='inputs-contract',
                                   run_id='checkpoint-contract')

        async def factory(actual_deps, saver):
            assert actual_deps is deps
            runtime = AgentRuntime(actual_deps, saver)
            assert runtime.graph.checkpointer is saver
            captured['saver'] = saver
            # Store a protocol fixture through the real R2 graph's checkpointer.
            # No analysis, model call, approval or customer write is performed.
            await runtime.graph.aupdate_state(runtime.config(receipt, context),
                {'disclosure': {'rcept_no': receipt}, 'status': 'DONE'}, as_node='receive')

            async def public(_):
                return await runtime.get_state(receipt, context=context)

            async def review(_):
                raise AssertionError('private execution is outside this checkpoint test')

            return SimpleNamespace(analyze_public=public, draft_review=review)

        async with open_executors(factory, deps, tmp_path/'injected.db') as ports:
            assert (await ports.public(None))['disclosure']['rcept_no'] == receipt
        with pytest.raises(ValueError):
            await captured['saver'].aget_tuple(AgentRuntime.config(receipt, context))

    asyncio.run(run())
