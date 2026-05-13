"""Agent-layer tests — exercise build_agent + AgentRunner without Ollama.

We inject a fake LLM that emits a deterministic AIMessage so the LangGraph
react loop completes in a single step. The point of this test is to verify
the wiring (prompt, runner, session integration), not the tool-calling
behaviour of the real model.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def fake_llm():
    """LLM stub that always returns the same final answer.

    Subclasses `FakeMessagesListChatModel` to add a no-op `bind_tools` —
    `create_react_agent` calls it, but our fake doesn't need to actually use
    the bound tools because it emits a final answer immediately.
    """
    try:
        from langchain_core.language_models.fake_chat_models import (
            FakeMessagesListChatModel,
        )
        from langchain_core.messages import AIMessage
    except ImportError:  # pragma: no cover
        pytest.skip("langchain-core not installed")

    class _ToolBindingFakeLLM(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    return _ToolBindingFakeLLM(
        responses=[
            AIMessage(
                content=(
                    "Order has been placed. Check your SFTP account.\n"
                    "Order ID: TEST-OK"
                )
            )
        ]
    )


def test_build_agent_returns_runnable(fake_llm):
    pytest.importorskip("langgraph")
    from mosdac_agent.agent import build_agent
    from mosdac_agent.client import MockMosdacClient
    from mosdac_agent.config import MosdacSettings
    from mosdac_agent.store import InMemoryStore

    agent = build_agent(
        settings=MosdacSettings(_env_file=None, mosdac_use_mock=True),
        store=InMemoryStore(),
        client=MockMosdacClient(),
        llm=fake_llm,
    )
    assert agent is not None
    assert hasattr(agent, "invoke")


def test_agent_runner_returns_final_answer(fake_llm):
    pytest.importorskip("langgraph")
    from mosdac_agent.agent import AgentRunner, build_agent
    from mosdac_agent.client import MockMosdacClient
    from mosdac_agent.config import MosdacSettings
    from mosdac_agent.store import InMemoryStore

    agent = build_agent(
        settings=MosdacSettings(_env_file=None, mosdac_use_mock=True),
        store=InMemoryStore(),
        client=MockMosdacClient(),
        llm=fake_llm,
    )
    runner = AgentRunner(agent=agent)
    reply = runner.chat(thread_id="t1", message="hi")
    assert "Order has been placed" in reply


def test_mosdac_agent_service_persists_history(fake_llm):
    pytest.importorskip("langgraph")
    from chat_api.session import InMemorySessionStore
    from mosdac_agent.agent import AgentRunner, MosdacAgentService, build_agent
    from mosdac_agent.client import MockMosdacClient
    from mosdac_agent.config import MosdacSettings
    from mosdac_agent.store import InMemoryStore

    sessions = InMemorySessionStore()
    agent = build_agent(
        settings=MosdacSettings(_env_file=None, mosdac_use_mock=True),
        store=InMemoryStore(),
        client=MockMosdacClient(),
        llm=fake_llm,
    )
    svc = MosdacAgentService(runner=AgentRunner(agent=agent), sessions=sessions)
    answer = svc.chat(session_id="s1", message="order something")
    assert "Order has been placed" in answer
    history = sessions.get("s1")
    assert history[0] == {"role": "user", "content": "order something"}
    assert history[1]["role"] == "assistant"
