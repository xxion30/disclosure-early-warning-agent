"""R4 owns checkpoint resources; R2 only receives the initialized saver."""
from contextlib import asynccontextmanager
from pathlib import Path


@asynccontextmanager
async def open_checkpointer(location):
    location=str(location)
    if location.startswith(('postgresql://','postgres://','postgresql+psycopg://')):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        location=location.replace('postgresql+psycopg://','postgresql://',1)
        async with AsyncPostgresSaver.from_conn_string(location) as saver:
            await saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        path=Path(location);path.parent.mkdir(parents=True,exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            await saver.setup()
            yield saver


@asynccontextmanager
async def open_backend_runtime(deps, location):
    from agent.runtime import AgentRuntime
    async with open_checkpointer(location) as saver:
        yield AgentRuntime(deps,saver)
