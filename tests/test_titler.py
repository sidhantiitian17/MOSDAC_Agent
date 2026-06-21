"""Tests for chat_api/titler.py — short conversation-title generation."""
from __future__ import annotations

from unittest.mock import MagicMock

from chat_api.titler import DEFAULT_TITLE, ConversationTitler, _clean_title


def test_clean_title_strips_quotes_and_trailing_punctuation():
    assert _clean_title('"Overview of INSAT 3D."') == "Overview of INSAT 3D"


def test_clean_title_uses_first_line_and_collapses_whitespace():
    assert _clean_title("  Cyclone   Tracking Data \nextra explanation") == "Cyclone Tracking Data"


def test_clean_title_caps_word_count():
    title = _clean_title("one two three four five six seven eight nine ten")
    assert len(title.split()) <= 8


def test_clean_title_empty_falls_back_to_default():
    assert _clean_title("   ") == DEFAULT_TITLE


def test_make_title_uses_llm_output():
    llm = MagicMock()
    resp = MagicMock()
    resp.content = "INSAT 3D Overview"
    llm.invoke.return_value = resp

    titler = ConversationTitler(llm=llm)
    assert titler.make_title("Tell me about INSAT 3D") == "INSAT 3D Overview"
    llm.invoke.assert_called_once()


def test_make_title_failsafe_on_llm_error():
    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError("LLM down")

    titler = ConversationTitler(llm=llm)
    assert titler.make_title("anything") == DEFAULT_TITLE
