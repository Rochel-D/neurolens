"""MCP client for the official, existing `mcp-server-fetch` server (stdio transport).

We launch `python -m mcp_server_fetch` as a subprocess, discover its tools with list_tools(),
and call its `fetch` tool. No custom MCP server is written for this project.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import sys

SERVER_ARGS = ["-m", "mcp_server_fetch", "--ignore-robots-txt"]
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def _params():
    from mcp import StdioServerParameters

    return StdioServerParameters(command=sys.executable, args=SERVER_ARGS)


async def _call(tool: str, args: dict) -> str:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            text = "".join(getattr(c, "text", "") for c in res.content)
            if getattr(res, "isError", False) or getattr(res, "is_error", False) or text.startswith("Error executing tool"):
                raise RuntimeError("MCP tool error: " + text[:200])
            return text


async def _list() -> list[dict]:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [{"name": t.name, "description": (t.description or "")[:160]} for t in tools.tools]


def _run(coro, timeout: float):
    # run in a worker thread so it works inside FastAPI's event loop too
    return _pool.submit(asyncio.run, coro).result(timeout=timeout)


def fetch(url: str, timeout: float = 25.0, max_length: int = 20000) -> str:
    text = _run(_call("fetch", {"url": url, "raw": True, "max_length": max_length}), timeout)
    # the fetch server prefixes a short header line ("Contents of <url>:") — keep the body only
    for marker in ("{", "<?xml", "<nlmSearchResult"):
        i = text.find(marker)
        if i >= 0:
            return text[i:]
    return text


def list_tools(timeout: float = 20.0) -> list[dict]:
    return _run(_list(), timeout)


def status() -> dict:
    try:
        tools = list_tools()
        return {"ok": True, "server": "mcp-server-fetch (official, stdio)", "tools": tools}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "server": "mcp-server-fetch (official, stdio)", "error": f"{type(e).__name__}: {e}"[:200]}
