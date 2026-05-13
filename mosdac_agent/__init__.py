"""MOSDAC AI-agent toolkit — a self-contained, deployment-portable package.

Public surface (stable API for alternate-domain deployments):
    MosdacSettings           — env-driven configuration
    build_agent              — LangGraph ReAct agent factory (Qwen via Ollama)
    AgentRunner              — thread-safe sync wrapper around the agent
    MosdacAgentService       — high-level chat service (parallels chat_api.ChatService)
    build_mosdac_router      — FastAPI router for /mosdac/* endpoints
    build_local_tools        — LangChain tools backed by the in-process client
    build_mcp_server         — FastMCP server exposing the same tools over MCP

Deploying to a new domain: override env vars (MOSDAC_* + AGENT_*) — no code changes.
"""
from mosdac_agent.config import MosdacSettings, mosdac_settings

__all__ = [
    "MosdacSettings",
    "mosdac_settings",
    "build_agent",
    "build_local_tools",
    "build_mcp_server",
    "build_mosdac_router",
    "MosdacAgentService",
    "AgentRunner",
]


def build_agent(*args, **kwargs):
    from mosdac_agent.agent.builder import build_agent as _impl
    return _impl(*args, **kwargs)


def build_local_tools(*args, **kwargs):
    from mosdac_agent.tools import build_local_tools as _impl
    return _impl(*args, **kwargs)


def build_mcp_server(*args, **kwargs):
    from mosdac_agent.mcp_server.server import build_mcp_server as _impl
    return _impl(*args, **kwargs)


def build_mosdac_router(*args, **kwargs):
    from mosdac_agent.integration.routes import build_mosdac_router as _impl
    return _impl(*args, **kwargs)


def MosdacAgentService(*args, **kwargs):  # noqa: N802  (factory facade)
    from mosdac_agent.integration.service import MosdacAgentService as _impl
    return _impl(*args, **kwargs)


def AgentRunner(*args, **kwargs):  # noqa: N802
    from mosdac_agent.agent.runner import AgentRunner as _impl
    return _impl(*args, **kwargs)
