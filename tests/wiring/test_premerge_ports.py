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
