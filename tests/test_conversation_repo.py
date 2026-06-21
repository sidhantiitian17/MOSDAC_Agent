"""Tests for chat_api/db — the per-user conversation store.

The ownership filtering (anti-IDOR) cases are the critical security tests: no
query may ever read or mutate another user's conversations or messages.
"""
from __future__ import annotations

import pytest

from chat_api.db.repository import ConversationNotFoundError
from chat_api.db.sqlite_repo import SQLiteConversationRepository


@pytest.fixture
def repo():
    r = SQLiteConversationRepository(":memory:")
    yield r
    r.close()


def test_create_and_list_for_owner_only(repo):
    c = repo.create_conversation("userA", "Hello world chat")
    convs = repo.list_conversations("userA")
    assert [x.id for x in convs] == [c.id]
    assert convs[0].title == "Hello world chat"
    assert repo.list_conversations("userB") == []  # other user sees nothing


def test_get_conversation_is_owner_scoped(repo):
    c = repo.create_conversation("userA")
    assert repo.get_conversation("userA", c.id).id == c.id
    assert repo.get_conversation("userB", c.id) is None  # IDOR blocked


def test_messages_round_trip_in_order(repo):
    c = repo.create_conversation("userA")
    repo.append_message("userA", c.id, "user", "q1")
    repo.append_message("userA", c.id, "assistant", "a1")
    repo.append_message("userA", c.id, "user", "q2")
    msgs = repo.list_messages("userA", c.id)
    assert [(m.role, m.content) for m in msgs] == [
        ("user", "q1"),
        ("assistant", "a1"),
        ("user", "q2"),
    ]


def test_append_message_bumps_updated_at(repo):
    c = repo.create_conversation("userA")
    before = repo.get_conversation("userA", c.id).updated_at
    repo.append_message("userA", c.id, "user", "hi")
    after = repo.get_conversation("userA", c.id).updated_at
    assert after >= before


def test_append_message_for_non_owner_raises_and_leaks_nothing(repo):
    c = repo.create_conversation("userA")
    with pytest.raises(ConversationNotFoundError):
        repo.append_message("userB", c.id, "user", "sneaky")
    assert repo.list_messages("userA", c.id) == []


def test_list_messages_for_non_owner_returns_empty(repo):
    c = repo.create_conversation("userA")
    repo.append_message("userA", c.id, "user", "secret")
    assert repo.list_messages("userB", c.id) == []


def test_update_title_is_owner_scoped(repo):
    c = repo.create_conversation("userA")
    repo.update_title("userB", c.id, "Hacked")  # not the owner → no-op
    assert repo.get_conversation("userA", c.id).title == "New chat"
    repo.update_title("userA", c.id, "Proper Title")
    assert repo.get_conversation("userA", c.id).title == "Proper Title"


def test_delete_is_owner_scoped(repo):
    c = repo.create_conversation("userA")
    assert repo.delete_conversation("userB", c.id) is False  # cannot delete others'
    assert repo.get_conversation("userA", c.id) is not None
    assert repo.delete_conversation("userA", c.id) is True
    assert repo.get_conversation("userA", c.id) is None


def test_delete_cascades_messages(repo):
    c = repo.create_conversation("userA")
    repo.append_message("userA", c.id, "user", "hi")
    repo.delete_conversation("userA", c.id)
    assert repo.list_messages("userA", c.id) == []


def test_list_conversations_ordered_most_recent_first(repo):
    c1 = repo.create_conversation("userA", "first")
    repo.create_conversation("userA", "second")
    repo.append_message("userA", c1.id, "user", "ping")  # bumps c1 to the top
    ids = [x.id for x in repo.list_conversations("userA")]
    assert ids[0] == c1.id
