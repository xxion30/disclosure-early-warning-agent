"""R4 checkpoint backend selection; R2 graph/runtime sources remain unchanged."""
from contextlib import asynccontextmanager


@asynccontextmanager
async def open_backend_runtime(deps, location):
    if str(location).startswith(('postgresql://', 'postgres://')):
        from agent.runtime import AgentRuntime
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        async with AsyncPostgresSaver.from_conn_string(str(location)) as saver:
            await saver.setup()
            yield AgentRuntime(deps, saver)
    else:
        from agent import open_runtime
        async with open_runtime(deps, str(location)) as runtime:
            yield runtime
