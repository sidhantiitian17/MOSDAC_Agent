"""Tests for chat_api/auth.py — the config-driven JWT claim adapter + deps.

No live Keycloak: ``normalize_user_data`` is pure and tested directly, and
``decode_token`` is exercised by mocking PyJWT's ``PyJWKClient`` / ``decode``.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from chat_api.config import ChatAPISettings


def _patch_settings(monkeypatch, **overrides):
    """Install a fresh settings object on the auth module (no real .env)."""
    fresh = ChatAPISettings(_env_file=None)
    for key, value in overrides.items():
        setattr(fresh, key, value)
    monkeypatch.setattr("chat_api.auth.chat_api_settings", fresh)
    return fresh


# ── normalize_user_data (the adapter) ────────────────────────────────────────

def test_normalize_user_data_default_keycloak_claims(monkeypatch):
    from chat_api import auth

    _patch_settings(monkeypatch)
    out = auth.normalize_user_data(
        {"sub": "abc-123", "preferred_username": "alice", "email": "a@x.org"}
    )
    assert out == {"id": "abc-123", "username": "alice", "email": "a@x.org"}


def test_normalize_user_data_is_config_driven(monkeypatch):
    """The adapter must read whatever claim names settings declare — proving the
    codebase has no hardcoded claim keys (a custom-claim IdP = .env change only)."""
    from chat_api import auth

    _patch_settings(
        monkeypatch,
        jwt_field_id="user_id",
        jwt_field_username="login",
        jwt_field_email="mail",
    )
    out = auth.normalize_user_data({"user_id": "u9", "login": "bob", "mail": "b@x.org"})
    assert out == {"id": "u9", "username": "bob", "email": "b@x.org"}


def test_normalize_user_data_missing_id_raises_401_naming_field(monkeypatch):
    from chat_api import auth

    _patch_settings(monkeypatch, jwt_field_id="sub")
    with pytest.raises(HTTPException) as ei:
        auth.normalize_user_data({"preferred_username": "alice"})
    assert ei.value.status_code == 401
    assert "sub" in ei.value.detail  # error names the expected field


def test_normalize_user_data_coerces_id_to_string(monkeypatch):
    from chat_api import auth

    _patch_settings(monkeypatch)
    out = auth.normalize_user_data({"sub": 12345})
    assert out["id"] == "12345"
    assert out["username"] is None and out["email"] is None


# ── decode_token (JWKS verification) ─────────────────────────────────────────

class _FakeKey:
    key = "fake-signing-key"


class _FakeJWKClient:
    def __init__(self, *args, **kwargs):
        pass

    def get_signing_key_from_jwt(self, token):
        return _FakeKey()


def test_decode_token_valid(monkeypatch):
    import jwt as pyjwt

    from chat_api import auth

    _patch_settings(monkeypatch, keycloak_issuer="https://kc/realms/m")
    auth.reset_jwk_cache()
    monkeypatch.setattr("jwt.PyJWKClient", _FakeJWKClient)
    monkeypatch.setattr(pyjwt, "decode", lambda *a, **k: {"sub": "u1"})

    assert auth.decode_token("tok")["sub"] == "u1"


def test_decode_token_expired_raises_401(monkeypatch):
    import jwt as pyjwt

    from chat_api import auth

    _patch_settings(monkeypatch, keycloak_issuer="https://kc/realms/m")
    auth.reset_jwk_cache()
    monkeypatch.setattr("jwt.PyJWKClient", _FakeJWKClient)

    def _raise(*a, **k):
        raise pyjwt.ExpiredSignatureError("expired")

    monkeypatch.setattr(pyjwt, "decode", _raise)
    with pytest.raises(HTTPException) as ei:
        auth.decode_token("tok")
    assert ei.value.status_code == 401


def test_decode_token_misconfigured_raises_500(monkeypatch):
    from chat_api import auth

    # auth on but no issuer / jwks url configured.
    _patch_settings(monkeypatch, keycloak_issuer="", keycloak_jwks_url="")
    auth.reset_jwk_cache()
    with pytest.raises(HTTPException) as ei:
        auth.decode_token("tok")
    assert ei.value.status_code == 500


# ── get_current_user / get_optional_user dependencies ────────────────────────

def test_get_optional_user_none_when_auth_disabled(monkeypatch):
    from chat_api import auth

    _patch_settings(monkeypatch, auth_enabled=False)
    assert auth.get_optional_user(authorization=None) is None
    assert auth.get_optional_user(authorization="Bearer anything") is None


def test_get_optional_user_none_when_no_token(monkeypatch):
    from chat_api import auth

    _patch_settings(monkeypatch, auth_enabled=True, keycloak_issuer="https://kc/realms/m")
    assert auth.get_optional_user(authorization=None) is None


def test_get_optional_user_returns_user_for_valid_token(monkeypatch):
    from chat_api import auth

    _patch_settings(monkeypatch, auth_enabled=True, keycloak_issuer="https://kc/realms/m")
    monkeypatch.setattr(
        auth, "decode_token",
        lambda t: {"sub": "u1", "preferred_username": "a", "email": "e@x"},
    )
    user = auth.get_optional_user(authorization="Bearer tok")
    assert user is not None
    assert user.id == "u1" and user.username == "a" and user.email == "e@x"


def test_get_optional_user_malformed_token_still_401(monkeypatch):
    """A bad token must NOT be silently downgraded to anonymous."""
    from chat_api import auth

    _patch_settings(monkeypatch, auth_enabled=True, keycloak_issuer="https://kc/realms/m")

    def _boom(token):
        raise HTTPException(status_code=401, detail="bad token")

    monkeypatch.setattr(auth, "decode_token", _boom)
    with pytest.raises(HTTPException) as ei:
        auth.get_optional_user(authorization="Bearer tok")
    assert ei.value.status_code == 401


def test_get_current_user_503_when_auth_disabled(monkeypatch):
    from chat_api import auth

    _patch_settings(monkeypatch, auth_enabled=False)
    with pytest.raises(HTTPException) as ei:
        auth.get_current_user(authorization="Bearer x")
    assert ei.value.status_code == 503


def test_get_current_user_401_without_token(monkeypatch):
    from chat_api import auth

    _patch_settings(monkeypatch, auth_enabled=True, keycloak_issuer="https://kc/realms/m")
    with pytest.raises(HTTPException) as ei:
        auth.get_current_user(authorization=None)
    assert ei.value.status_code == 401


def test_get_current_user_returns_user_for_valid_token(monkeypatch):
    from chat_api import auth

    _patch_settings(monkeypatch, auth_enabled=True, keycloak_issuer="https://kc/realms/m")
    monkeypatch.setattr(
        auth, "decode_token", lambda t: {"sub": "u7", "preferred_username": "z"}
    )
    user = auth.get_current_user(authorization="Bearer tok")
    assert user.id == "u7" and user.username == "z"
