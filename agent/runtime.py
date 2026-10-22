"""Graph 실행 경계: API(R4)가 부르는 Python 진입점. (담당: R2, 연결은 R4)

공시 1건 = 스레드 1개(thread_id = rcept_no). 승인 대기·재개는 DARTCH에서 삭제했다.
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from langsmith import trace

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agent.execution import Execution, current_execution
from agent.graph import build_graph
from agent.state import initial_state


class AgentRuntime:
    def __init__(self, deps, checkpointer):
        self.deps = deps
        self.graph = build_graph(deps, checkpointer)
        self._locks = {}

    @staticmethod
    def config(rcept_no):
        # This is a graph-superstep safeguard, not the separate eight-AI-turn budget.
        return {"configurable": {"thread_id": rcept_no}, "recursion_limit": 256}

    def _lock(self, rcept_no):
        return self._locks.setdefault(rcept_no, asyncio.Lock())

    async def get_state(self, rcept_no):
        """Internal full snapshot. API는 응답에 넣기 전에 회사(테넌트)별로 걸러야 한다."""
        snapshot = await self.graph.aget_state(self.config(rcept_no))
        return dict(snapshot.values)

    async def _invoke(self, rcept_no, value, trace_id):
        scope = Execution(self.deps.execution_seconds, self.deps.max_model_turns)
        token = current_execution.set(scope)
        config = self.config(rcept_no)
        config["metadata"] = {"rcept_no": rcept_no, "trace_id": trace_id}
        try:
            initial = isinstance(value, dict)
            previous = {} if initial else await self.get_state(rcept_no)
            with trace("disclosure_agent" if initial else "disclosure_agent_resume",
                       run_id=trace_id if initial else None,
                       parent=previous.get("trace_headers"),
                       inputs={"rcept_no": rcept_no}, metadata=config["metadata"]) as run:
                if initial:
                    value["trace_headers"] = run.to_headers()
                await self.graph.ainvoke(value, config=config)
                state = await self.get_state(rcept_no)
                run.add_metadata({"corp_name": state["disclosure"].get("corp_name"),
                                  "risk_level": state.get("risk_level")})
                run.end(outputs={"status": state["status"], "risk_level": state.get("risk_level")})
                return state
        finally:
            current_execution.reset(token)

    async def start(self, rcept_no: str):
        if not isinstance(rcept_no, str) or not rcept_no.strip():
            raise ValueError("접수번호가 필요합니다")
        async with self._lock(rcept_no):
            snapshot = await self.graph.aget_state(self.config(rcept_no))
            existing = dict(snapshot.values)
            if existing:
                # 노드 실행 중에 프로세스가 죽은 실행: 다음 노드가 남아 있다.
                # 큐가 같은 공시를 다시 넘겨 줬을 때 조용히 "끝난 것"으로 돌려주면 공시가 사라지므로, 마지막 체크포인트부터 이어서 실행한다.
                if snapshot.next:
                    return await self._invoke(rcept_no, None, existing["trace_id"])
                # 끝난 실행은 암묵적으로 다시 시작하지 않는다.
                return existing
            state = initial_state(rcept_no, str(uuid4()))
            return await self._invoke(rcept_no, state, state["trace_id"])


@asynccontextmanager
async def open_runtime(deps, checkpoint_db=None):
    if checkpoint_db is None:
        from shared.config import get_settings
        checkpoint_db = get_settings().CHECKPOINT_DB
    path = Path(checkpoint_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        yield AgentRuntime(deps, saver)
