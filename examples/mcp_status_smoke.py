"""Minimal MCP stdio smoke test.

Run from the repo root after installing the MCP extra:

    python examples/mcp_status_smoke.py

The script lists server tools and calls get_metis_status against a temp data
directory. It does not run a job search or touch your real tracker.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    data_dir = Path(tempfile.mkdtemp(prefix="metis-mcp-smoke-"))
    profile_path = data_dir / "profile.yaml"
    profile_path.write_text("candidate:\n  name: MCP Smoke\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "METIS_DATA_DIR": str(data_dir),
            "METIS_PROFILE": str(profile_path),
            "TRACKER_PATH": str(data_dir / "applications.xlsx"),
            "METIS_LLM_PROVIDER": env.get("METIS_LLM_PROVIDER", "openai"),
            "OPENAI_API_KEY": env.get("OPENAI_API_KEY", "sk-smoke"),
            "GMAIL_ADDRESS": env.get("GMAIL_ADDRESS", "smoke@example.com"),
            "GMAIL_APP_PASSWORD": env.get("GMAIL_APP_PASSWORD", "smoke-password"),
        }
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "metis.mcp_server"],
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            status = await session.call_tool(
                "get_metis_status",
                {
                    "data_dir": str(data_dir),
                    "profile_path": str(profile_path),
                    "tracker_path": str(data_dir / "applications.xlsx"),
                },
            )

    print("Tools:", ", ".join(tool.name for tool in tools.tools))
    print(status.content[0].text)
    print(f"Temp data dir: {data_dir}")


if __name__ == "__main__":
    asyncio.run(main())
