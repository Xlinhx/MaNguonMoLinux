from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import Settings
from .sandbox import cleanup_job_containers


LLM_SECRET_ENV_KEYS = (
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
)


class McpLabClient:
    """Async MCP client speaking stdio to backend.app.mcp_server with end-to-end timeouts."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _server_params(self, job_id: str | None = None) -> StdioServerParameters:
        env = dict(os.environ)
        for secret_key in LLM_SECRET_ENV_KEYS:
            env.pop(secret_key, None)
        env["SANDBOX_LAB_ROOT"] = str(self.settings.project_root)
        env["SANDBOX_LAB_RUNTIME_DIR"] = str(self.settings.runtime_dir)
        env["SANDBOX_LAB_DB_PATH"] = str(self.settings.db_path)
        env["SANDBOX_LAB_NGINX_LOG_PATH"] = str(self.settings.nginx_log_path)
        env["SANDBOX_LAB_SNAPSHOTS_DIR"] = str(self.settings.snapshots_dir)
        env["SANDBOX_LAB_WORKER_IMAGE"] = self.settings.worker_image
        if job_id:
            env["SANDBOX_LAB_JOB_ID"] = job_id
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "backend.app.mcp_server"],
            env=env,
            cwd=str(self.settings.project_root),
        )

    async def list_tools(self, timeout_s: float = 15.0) -> list[dict[str, Any]]:
        params = self._server_params()

        async def _run() -> list[dict[str, Any]]:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    resp = await session.list_tools()
                    return [
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "inputSchema": t.inputSchema,
                        }
                        for t in resp.tools
                    ]

        return await asyncio.wait_for(_run(), timeout=max(0.5, float(timeout_s)))

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout_s: float = 15.0,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        params = self._server_params(job_id=job_id)

        async def _run() -> dict[str, Any]:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
                    texts: list[str] = []
                    for block in result.content:
                        txt = getattr(block, "text", None)
                        if isinstance(txt, str):
                            texts.append(txt)
                    joined = "\n".join(texts).strip()
                    if result.isError:
                        raise RuntimeError(joined or f"MCP tool {tool_name} failed")
                    return json.loads(joined)

        try:
            return await asyncio.wait_for(_run(), timeout=max(0.5, float(timeout_s)))
        except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError):
            if job_id:
                cleanup_job_containers(None, job_id)
            raise
