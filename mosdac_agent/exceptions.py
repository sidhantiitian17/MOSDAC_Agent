"""Typed exceptions for the MOSDAC agent toolkit.

Tool implementations raise these; the MCP server / agent / FastAPI layers
translate them into user-facing errors (ToolError, HTTPException, chat reply)
at their respective boundaries.
"""
from __future__ import annotations


class MosdacError(Exception):
    """Base class for any MOSDAC agent error."""


class ValidationError(MosdacError):
    """Input validation failed (bad dates, missing AOI, oversized range, ...)."""


class AuthError(MosdacError):
    """MOSDAC SSO login failed. DO NOT auto-retry — 3 failures = 1-hour lock."""


class RateLimitError(MosdacError):
    """User has exceeded the per-hour order quota."""


class NotFoundError(MosdacError):
    """Requested order / dataset / resource does not exist."""


class UpstreamError(MosdacError):
    """MOSDAC backend returned an unexpected status / network failed."""
