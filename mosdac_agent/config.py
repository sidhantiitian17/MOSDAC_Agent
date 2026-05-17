"""Env-driven configuration for the MOSDAC agent toolkit.

Every value is overridable via environment variables (or `.env`) so the same
container image can be re-deployed to MOSDAC, a sandbox, or an alternate domain
without code changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MosdacSettings(BaseSettings):
    """All MOSDAC + agent settings in one place."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── MOSDAC API ──────────────────────────────────────────────────────────
    mosdac_base_url: str = "https://mosdac.gov.in"
    mosdac_auth_url: str = "https://mosdac.gov.in/auth/realms/Mosdac"
    mosdac_username: str = ""
    mosdac_password: str = ""
    mosdac_client_id: str = "mosdac-portal"
    mosdac_use_mock: bool = True  # default to mock so tests + dev work offline

    # ── MCP server ──────────────────────────────────────────────────────────
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765
    mcp_transport: Literal["stdio", "streamable-http"] = "streamable-http"
    mcp_server_name: str = "mosdac-order-server"

    # ── Agent / LLM (Tabby ML — reuses TABBY_* from .env by default) ─────────
    # Each field falls back to the shared TABBY_* var so the credential lives
    # in .env exactly once. Set AGENT_LLM_* only to point the agent at an LLM
    # different from the rest of the stack.
    agent_llm_base_url: str = Field(
        default="http://localhost:8080/v1",
        validation_alias=AliasChoices("agent_llm_base_url", "tabby_base_url"),
    )
    agent_llm_model: str = Field(
        default="Qwen2-1.5B-Instruct",
        validation_alias=AliasChoices("agent_llm_model", "tabby_model"),
    )
    # Credential — never hardcoded. Resolved from AGENT_LLM_API_KEY or TABBY_API_TOKEN.
    agent_llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("agent_llm_api_key", "tabby_api_token"),
    )
    agent_llm_temperature: float = 0.1
    agent_llm_num_ctx: int = 8192
    agent_use_local_tools: bool = True
    """If true the agent calls Python tools in-process (no MCP transport).
    If false the agent connects to the MCP server via langchain-mcp-adapters."""
    agent_recursion_limit: int = 12

    # ── Safety limits ───────────────────────────────────────────────────────
    max_orders_per_user_per_hour: int = 10
    max_files_per_order: int = 100
    max_date_range_days: int = 92

    # ── Persistence ─────────────────────────────────────────────────────────
    data_dir: str = "./data"
    idempotency_db_filename: str = "idempotency.sqlite"

    # ── HTTP integration ────────────────────────────────────────────────────
    enable_mosdac_endpoint: bool = False
    mosdac_route_prefix: str = "/mosdac"
    require_sso_header: bool = False
    sso_header_name: str = "X-MOSDAC-User"
    sso_dev_user: str = "dev-user"

    # ── Branding (per-domain overrides) ─────────────────────────────────────
    bot_name: str = "MOSDAC-Bot"
    final_success_sentence: str = "Order has been placed. Check your SFTP account."
    sftp_base_url: str = "sftp://ftp.mosdac.gov.in"

    # ── Custom data files (extension points) ────────────────────────────────
    catalog_json_path: str = ""
    regions_json_path: str = ""

    def db_path(self) -> Path:
        d = Path(self.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d / self.idempotency_db_filename

    def mcp_url(self) -> str:
        return f"http://{self.mcp_host}:{self.mcp_port}/mcp/"


mosdac_settings = MosdacSettings()
