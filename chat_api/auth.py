"""Keycloak / OIDC authentication with a config-driven user **adapter**.

Design goal: the rest of the application never sees a raw Keycloak token and never
references a claim name. ``decode_token`` verifies the JWT against the realm's JWKS
keys, ``normalize_user_data`` maps the IdP's claim names (read from settings, never
hardcoded) onto a stable internal shape, and route handlers / the DB layer consume
only :class:`NormalizedUser`. Switching identity providers — or pointing at a
government portal that issues custom claims — is therefore an ``.env`` change, not a
code change (set ``JWT_FIELD_ID`` / ``JWT_FIELD_USERNAME`` / ``JWT_FIELD_EMAIL``).

PyJWT is imported lazily so a deployment that leaves ``CHAT_API_AUTH_ENABLED=false``
(the default) never needs the crypto extra installed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, status

from chat_api.config import chat_api_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NormalizedUser:
    """The ONLY user shape the application uses. ``id`` is the ownership key."""

    id: str
    username: Optional[str] = None
    email: Optional[str] = None
    is_anonymous: bool = False


# ── JWKS signing-key client (cached per URL) ──────────────────────────────────

_jwk_clients: dict = {}


def _jwk_client():
    """Return a cached PyJWKClient for the configured JWKS URL.

    The client caches signing keys per ``kid`` and refreshes them lazily, so a key
    rotation in Keycloak is picked up without a restart.
    """
    from jwt import PyJWKClient  # lazy: only needed when auth is enabled

    url = chat_api_settings.effective_jwks_url()
    if not url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication is misconfigured: no Keycloak issuer or JWKS URL set.",
        )
    client = _jwk_clients.get(url)
    if client is None:
        client = PyJWKClient(
            url, cache_keys=True, lifespan=chat_api_settings.jwks_cache_seconds
        )
        _jwk_clients[url] = client
    return client


def reset_jwk_cache() -> None:
    """Drop cached JWKS clients (used by tests after changing settings)."""
    _jwk_clients.clear()


def decode_token(token: str) -> dict:
    """Verify a JWT against the JWKS keys and return its claims.

    Enforces signature, ``exp``, and (when configured) ``aud`` / ``iss``. The
    algorithm allow-list blocks the ``alg:none`` and HS/RS confusion attacks. Any
    failure raises ``HTTPException(401)`` — a forged or expired token is rejected.
    """
    import jwt  # lazy import

    try:
        signing_key = _jwk_client().get_signing_key_from_jwt(token)
        audiences = chat_api_settings.keycloak_audiences_list()
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=chat_api_settings.jwt_algorithms_list(),
            audience=audiences or None,
            issuer=chat_api_settings.keycloak_issuer or None,
            options={"require": ["exp"], "verify_aud": bool(audiences)},
        )
    except HTTPException:
        raise
    except jwt.PyJWTError as exc:
        # Log the specific cause server-side, but return a generic message (M5):
        # the library detail (claim names, alg, validation specifics) is recon that
        # aids token-forgery attempts and must not reach the client.
        logger.info("JWT validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token."
        ) from exc
    except Exception as exc:  # noqa: BLE001 — JWKS fetch/network failures
        logger.warning("Token verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token could not be verified.",
        ) from exc


def lookup_claim(claims: dict, spec: str):
    """Resolve a claim value from a decoded token using a configurable *spec*.

    The spec (set in ``.env`` — e.g. ``JWT_FIELD_USERNAME``) is intentionally
    expressive so that *custom token payloads need zero code changes*. It supports
    the shapes real identity providers actually emit:

    * a plain claim name ............... ``preferred_username``
    * a dotted path into nested objects  ``user_info.preferred_username``
    * comma-separated **fallbacks**, tried left-to-right until one is non-empty
      ``preferred_username,name,email``

    Returns the first non-empty value found, else ``None``. A plain single name
    behaves exactly like ``claims.get(name)`` — fully backward compatible.
    """
    if not spec:
        return None
    for path in (p.strip() for p in spec.split(",")):
        if not path:
            continue
        value = claims
        for segment in path.split("."):
            if isinstance(value, dict) and segment in value:
                value = value[segment]
            else:
                value = None
                break
        if value not in (None, ""):
            return value
    return None


def normalize_user_data(decoded_token: dict) -> dict:
    """ADAPTER: extract id/username/email using the *configured* claim specs.

    Decoupled from Keycloak's structure — a different IdP (or a portal that issues
    custom/nested claims) needs only an ``.env`` change. Each field spec supports
    nested dotted paths and comma-separated fallbacks (see :func:`lookup_claim`).
    Raises ``HTTPException(401)`` naming the expected field when the id claim is
    absent, so misconfiguration is diagnosable from the error message.
    """
    s = chat_api_settings
    uid = lookup_claim(decoded_token, s.jwt_field_id)
    if uid is None or uid == "":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"Token is missing the required id claim '{s.jwt_field_id}'. "
                f"Set JWT_FIELD_ID (or CHAT_API_JWT_FIELD_ID) to the claim your "
                f"identity provider uses for the stable user id. A nested path "
                f"(user_info.sub) or fallbacks (sub,uid) are allowed."
            ),
        )
    return {
        "id": str(uid),
        "username": lookup_claim(decoded_token, s.jwt_field_username),
        "email": lookup_claim(decoded_token, s.jwt_field_email),
    }


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _user_from_token(token: str) -> NormalizedUser:
    data = normalize_user_data(decode_token(token))
    return NormalizedUser(id=data["id"], username=data["username"], email=data["email"])


def get_current_user(
    authorization: Optional[str] = Header(default=None),
) -> NormalizedUser:
    """Required-auth dependency for the per-user conversation endpoints."""
    if not chat_api_settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not enabled on this deployment.",
        )
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token."
        )
    return _user_from_token(token)


def get_optional_user(
    authorization: Optional[str] = Header(default=None),
) -> Optional[NormalizedUser]:
    """Anonymous-allowed dependency for /chat.

    No token (or auth disabled) → ``None`` → ephemeral, non-persisted session. A
    *malformed* token still raises 401: a real session is never silently downgraded
    to anonymous.
    """
    if not chat_api_settings.auth_enabled:
        return None
    token = _extract_bearer(authorization)
    if not token:
        return None
    return _user_from_token(token)
