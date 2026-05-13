"""LangGraph ReAct agent + thread-safe runner + chat-style service facade.

Three layers, each independently usable:

* `build_agent(...)`         — pure factory; returns a compiled LangGraph agent.
* `AgentRunner(agent)`       — thread-safe sync wrapper exposing `chat(...)`.
* `MosdacAgentService(...)`  — drop-in replacement for `chat_api.ChatService`
                               that stores history in a `SessionStore`.

LLM selection is env-driven (defaults to local Qwen on Ollama using the
OpenAI-compatible endpoint already supported by `langchain-openai`).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, List, Optional

from mosdac_agent.client import MosdacClient, build_default_client
from mosdac_agent.config import MosdacSettings, mosdac_settings
from mosdac_agent.store import Store, build_default_store
from mosdac_agent.tools import ToolContext, build_local_tools

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are {bot_name}, an assistant that helps registered
MOSDAC users place satellite-data orders.

You have these tools:
  search_products(query, satellite, sensor)
  place_order(dataset_id, start_date, end_date, bounding_box | state_or_region,
              level_format, delivery)
  check_order_status(order_id)
  list_my_orders()

Hard rules:
1. ALWAYS resolve a product name to a dataset_id with search_products BEFORE
   calling place_order. Never invent dataset_ids.
2. Dates must be in YYYY-MM-DD. If the user gives a natural-language date,
   convert it; if ambiguous, ask one short clarifying question.
3. For Indian states/regions, prefer state_or_region; the tool resolves the
   bounding box.
4. Default delivery is SFTP. Mention that the user will retrieve files from
   the SFTP host using their MOSDAC credentials.
5. After a successful place_order, your FINAL reply must START with exactly:
   "{final_success_sentence}"
   followed by a one-line summary with the order_id.
6. Never reveal credentials or raw API responses. Be concise.
"""


def _render_prompt(settings: MosdacSettings) -> str:
    return SYSTEM_PROMPT.format(
        bot_name=settings.bot_name,
        final_success_sentence=settings.final_success_sentence,
    )


def _build_default_llm(settings: MosdacSettings):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.agent_llm_model,
        api_key=settings.agent_llm_api_key,
        base_url=settings.agent_llm_base_url,
        temperature=settings.agent_llm_temperature,
        streaming=False,
    )


def build_agent(
    *,
    settings: Optional[MosdacSettings] = None,
    store: Optional[Store] = None,
    client: Optional[MosdacClient] = None,
    llm: Any = None,
    user: str = "default",
    tools: Optional[list] = None,
):
    """Construct a LangGraph ReAct agent ready to chat.

    Heavy deps (langgraph, langchain) are imported lazily so this module can
    still be imported in environments that only need the configuration or
    tool-impl layer.
    """
    s = settings or mosdac_settings
    st = store or build_default_store()
    cl = client or build_default_client(s)
    ctx = ToolContext(user=user, store=st, client=cl, settings=s)

    if tools is None:
        if s.agent_use_local_tools:
            tools = build_local_tools(ctx)
        else:
            tools = _load_mcp_tools(s)

    llm = llm or _build_default_llm(s)

    try:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.prebuilt import create_react_agent
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "langgraph is required for the agent. "
            "Install: pip install langgraph"
        ) from exc

    memory = MemorySaver()
    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=_render_prompt(s),
        checkpointer=memory,
    )
    return agent


def _load_mcp_tools(settings: MosdacSettings) -> list:  # pragma: no cover
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:
        raise RuntimeError(
            "langchain-mcp-adapters is required when AGENT_USE_LOCAL_TOOLS=false. "
            "Install: pip install langchain-mcp-adapters"
        ) from exc
    mcp_client = MultiServerMCPClient(
        {
            "mosdac": {
                "transport": "streamable_http",
                "url": settings.mcp_url(),
            }
        }
    )
    return asyncio.run(mcp_client.get_tools())


@dataclass
class AgentRunner:
    """Thread-safe synchronous wrapper around a LangGraph agent.

    `chat(thread_id, message)` returns the final assistant string. Concurrent
    calls are serialised per-thread by the underlying MemorySaver checkpointer.
    """

    agent: Any
    recursion_limit: int = 12
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def chat(self, thread_id: str, message: str) -> str:
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.recursion_limit,
        }
        payload = {"messages": [{"role": "user", "content": message}]}
        with self._lock:
            result = self.agent.invoke(payload, config=config)
        messages = result.get("messages") if isinstance(result, dict) else None
        if not messages:
            return ""
        last = messages[-1]
        content = getattr(last, "content", None)
        if content is None and isinstance(last, dict):
            content = last.get("content", "")
        return content if isinstance(content, str) else str(content)


class MosdacAgentService:
    """High-level chat service — same call shape as `chat_api.ChatService`."""

    def __init__(
        self,
        runner: AgentRunner,
        sessions,
        *,
        settings: Optional[MosdacSettings] = None,
        max_history_turns: Optional[int] = None,
    ) -> None:
        self._runner = runner
        self._sessions = sessions
        self._settings = settings or mosdac_settings
        self._max_history = max_history_turns or 10

    def chat(self, session_id: str, message: str) -> str:
        self._sessions.trim(session_id, self._max_history)
        thread_id = f"mosdac:{session_id}"
        answer = self._runner.chat(thread_id=thread_id, message=message)
        self._sessions.append(session_id, "user", message)
        self._sessions.append(session_id, "assistant", answer)
        return answer

    def history(self, session_id: str) -> List[dict]:
        return list(self._sessions.get(session_id))

    def clear(self, session_id: str) -> None:
        self._sessions.clear(session_id)
