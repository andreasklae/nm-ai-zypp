"""Combined ASGI app for the Tripletex MCP service.

Routes:
  POST /solve  — Gemini agent endpoint; accepts a task prompt + Tripletex credentials,
                 solves the task using the same Tripletex tools as the main agent.
  POST /mcp    — Streamable MCP endpoint; exposes all Tripletex tools for any MCP client
                 (Claude Desktop, Claude Code, etc.) using env-var credentials.
"""
from __future__ import annotations

from fastapi import FastAPI

from tripletex_mcp.server import mcp
from tripletex_mcp.solve import router as solve_router

app = FastAPI(
    title="Tripletex MCP Server",
    version="0.1.0",
    description="Tripletex accounting tools as MCP + a Gemini-powered /solve endpoint.",
)

# /solve — Gemini agent (per-request credentials from the request body)
app.include_router(solve_router)

# /mcp — Streamable MCP protocol (env-var credentials, for Claude Desktop / Claude Code)
# FastAPI routes registered above take priority, so /solve is never caught by the MCP app.
app.mount("/", mcp.streamable_http_app())
