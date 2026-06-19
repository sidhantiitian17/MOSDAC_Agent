"""Tests for rolling conversation summary memory — mocked LLM / in-memory store."""
from __future__ import annotations

from unittest.mock import MagicMock

from graph_rag.config import settings as graph_settings


# ── ConversationSummarizer (graph_rag/chat/summarizer.py) ────────────────────

def test_summarizer_no_evicted_turns_is_noop():
    from graph_rag.chat.summarizer import ConversationSummarizer

    llm = MagicMock()
    s = ConversationSummarizer(llm=llm)
    assert s.update("prev summary", []) == "prev summary"
    llm.invoke.assert_not_called()


def test_summarizer_folds_turns_via_single_llm_call():
    from graph_rag.chat.summarizer import ConversationSummarizer

    llm = MagicMock()
    resp = MagicMock()
    resp.content = "User asked about Oceansat-2 sensors; OCM and OSCAT were discussed."
    llm.invoke.return_value = resp

    s = ConversationSummarizer(llm=llm)
    out = s.update(
        "",
        [
            {"role": "user", "content": "What sensors does Oceansat-2 carry?"},
            {"role": "assistant", "content": "OCM and OSCAT."},
        ],
    )
    assert out == "User asked about Oceansat-2 sensors; OCM and OSCAT were discussed."
    llm.invoke.assert_called_once()


def test_summarizer_degrades_to_transcript_on_llm_failure():
    from graph_rag.chat.summarizer import ConversationSummarizer

    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError("tabby down")
    s = ConversationSummarizer(llm=llm)

    out = s.update("earlier", [{"role": "user", "content": "and the swath?"}])
    assert "earlier" in out
    assert "and the swath?" in out  # context preserved, not lost


# ── ChatService overflow → summary (chat_api/service.py) ─────────────────────

def _make_service():
    from chat_api.service import ChatService
    from chat_api.session import InMemorySessionStore

    retriever = MagicMock()
    retriever.retrieve.return_value = {"graph_context": "g", "vector_context": "v"}
    chain = MagicMock()
    chain.invoke.return_value = "ans"
    sessions = InMemorySessionStore()
    service = ChatService(retriever=retriever, chain=chain, llm=MagicMock(), sessions=sessions)
    return service, sessions


def test_service_summarizes_overflow_and_prefixes_summary(monkeypatch):
    monkeypatch.setattr(graph_settings, "enable_conversation_summary", True)
    monkeypatch.setattr(graph_settings, "summary_keep_recent_turns", 1)

    service, sessions = _make_service()
    # Inject a fake summarizer so no live LLM is needed.
    fake = MagicMock()
    fake.update.return_value = "MEMORY"
    service._summarizer = fake

    # keep=1 → store keeps 2 entries; overflow summarization kicks in by turn 3.
    service.chat("s", "q1")
    service.chat("s", "q2")
    service.chat("s", "q3")

    assert fake.update.called
    assert sessions.get_summary("s") == "MEMORY"
    prefix = service._build_history_prefix("s")
    assert "Summary of earlier conversation: MEMORY" in prefix


def test_service_summary_disabled_by_default_keeps_old_trim_behavior():
    service, sessions = _make_service()
    service._summarizer = MagicMock()  # should never be called when disabled

    for i in range(3):
        service.chat("s", f"q{i}")

    assert service._summarizer.update.call_count == 0
    assert sessions.get_summary("s") == ""


# ── GraphRagChatbot CLI overflow (graph_rag/chat/chatbot.py) ─────────────────

def test_cli_chatbot_folds_evicted_turn_into_summary(monkeypatch):
    monkeypatch.setattr(graph_settings, "enable_conversation_summary", True)
    from graph_rag.chat.chatbot import GraphRagChatbot

    chain = MagicMock()
    chain.invoke.return_value = "answer"
    bot = GraphRagChatbot(window=1, chain=chain, retriever=MagicMock())
    fake = MagicMock()
    fake.update.return_value = "MEM"
    bot._summarizer = fake

    bot.chat("first question")
    bot.chat("second question")  # window full → first turn folded into summary

    assert fake.update.called
    assert bot.summary == "MEM"
