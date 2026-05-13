"""Tests for chat/chatbot.py — multi-turn, memory, error recovery."""
from __future__ import annotations

from collections import deque
from unittest.mock import MagicMock


def _make_bot_with_canned_responses(responses: list[str]):
    from graph_rag.chat.chatbot import GraphRagChatbot

    bot = GraphRagChatbot.__new__(GraphRagChatbot)
    bot.window = 10
    bot.history = deque(maxlen=10)
    bot.retriever = MagicMock()
    chain = MagicMock()
    chain.invoke.side_effect = lambda payload: responses.pop(0)
    bot.chain = chain
    return bot


def test_single_turn():
    bot = _make_bot_with_canned_responses(["Hi there!"])
    out = bot.chat("Hello")
    assert out == "Hi there!"
    assert len(bot.history) == 1


def test_multi_turn_keeps_history():
    bot = _make_bot_with_canned_responses(["Apple acquired Beats.", "Yes, in 2014."])
    bot.chat("What did Apple acquire?")
    bot.chat("When?")
    assert len(bot.history) == 2
    assert bot.history[0].user == "What did Apple acquire?"
    assert bot.history[1].assistant == "Yes, in 2014."


def test_reset_clears_history():
    bot = _make_bot_with_canned_responses(["a", "b"])
    bot.chat("x")
    bot.chat("y")
    assert len(bot.history) == 2
    bot.reset()
    assert len(bot.history) == 0


def test_error_in_chain_does_not_crash():
    bot = _make_bot_with_canned_responses([])
    bot.chain.invoke.side_effect = RuntimeError("boom")
    out = bot.chat("hello")
    assert "error" in out.lower()
    assert len(bot.history) == 1
