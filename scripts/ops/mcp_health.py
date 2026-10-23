"""Container readiness: MCP initialize + list tools, without model/data calls."""
import asyncio
import sys
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def check(url):
    async with streamablehttp_client(url) as (reader, writer, _):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.list_tools()
            if not result.tools:
                raise RuntimeError('MCP server has no tools')


if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(check(sys.argv[1]), timeout=8))
