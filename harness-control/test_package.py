#!/usr/bin/env python3
"""Exercise the built command with hostile ambient Python configuration."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


EXPECTED = {
    "get_status",
    "get_usage",
    "record_checkpoint",
    "self_compact",
    "self_exit",
    "send_to_self",
    "read_own_pane",
    "get_operation",
    "cancel_operation",
}


async def probe(command: str) -> None:
    with tempfile.TemporaryDirectory(prefix="harness-control-package-") as directory:
        home = Path(directory)
        hostile = home / "hostile-python"
        hostile.mkdir()
        (hostile / "mcp.py").write_text("raise AssertionError('ambient mcp imported')\n")
        (hostile / "harness_identity.py").write_text(
            "raise AssertionError('ambient identity reader imported')\n"
        )
        env = {
            "HOME": str(home),
            "PATH": "/ambient-path-must-not-be-required",
            "PYTHONHOME": str(hostile),
            "PYTHONPATH": str(hostile),
            "AGENT_SKILLS_ROOT": str(hostile),
        }
        old_cwd = Path.cwd()
        os.chdir(home)
        started = time.monotonic()
        try:
            params = StdioServerParameters(command=command, args=[], env=env)
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    names = {tool.name for tool in (await client.list_tools()).tools}
                    missing = EXPECTED - names
                    if missing:
                        raise AssertionError(f"missing tools: {sorted(missing)}")
                    result = await client.call_tool("get_status", {})
                    if result.isError:
                        raise AssertionError(result)
                    payload = json.loads(next(item.text for item in result.content
                                              if item.type == "text"))
                    if payload.get("state") != "unavailable":
                        raise AssertionError(payload)
        finally:
            os.chdir(old_cwd)
        print(f"package MCP probe passed in {time.monotonic() - started:.3f}s")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_absolute():
        raise SystemExit("usage: test_package.py /absolute/path/to/harness-control")
    asyncio.run(probe(sys.argv[1]))
