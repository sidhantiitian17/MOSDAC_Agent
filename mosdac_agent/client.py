"""MOSDAC backend clients.

Two concrete implementations:

* `HttpMosdacClient` — real backend over httpx with cached Keycloak token.
  Endpoint paths below are placeholders; align with the Order API PDF.
* `MockMosdacClient` — fully in-process; never hits the network.

Pick one via `mosdac_settings.mosdac_use_mock`.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Protocol

import httpx

from mosdac_agent.config import MosdacSettings, mosdac_settings
from mosdac_agent.exceptions import AuthError, NotFoundError, UpstreamError


class MosdacClient(Protocol):
    """Anything that knows how to talk to MOSDAC."""

    def search(self, query: str, satellite: Optional[str], sensor: Optional[str]) -> List[dict]: ...
    def place_order(self, payload: dict, idempotency_key: str) -> dict: ...
    def get_order(self, order_id: str) -> dict: ...


@dataclass
class _Session:
    client: Optional[httpx.Client] = None
    expires_at: float = 0.0


class HttpMosdacClient:
    """httpx-backed client. Endpoint paths are illustrative; align with the
    Order API PDF (auth structure follows the public manual)."""

    def __init__(self, settings: MosdacSettings) -> None:
        self._settings = settings
        self._session = _Session()
        self._lock = threading.RLock()

    def _login(self) -> httpx.Client:
        with self._lock:
            now = time.time()
            if self._session.client and self._session.expires_at > now:
                return self._session.client
            if not (self._settings.mosdac_username and self._settings.mosdac_password):
                raise AuthError("MOSDAC credentials are not configured on the server.")
            client = httpx.Client(
                base_url=self._settings.mosdac_base_url,
                timeout=60.0,
                follow_redirects=True,
            )
            r = client.post(
                "/auth/realms/Mosdac/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": self._settings.mosdac_client_id,
                    "username": self._settings.mosdac_username,
                    "password": self._settings.mosdac_password,
                },
            )
            if r.status_code != 200:
                raise AuthError(
                    f"MOSDAC login failed ({r.status_code}). "
                    "Three failures = 1-hour lockout, so do NOT retry."
                )
            tok = r.json()
            client.headers["Authorization"] = f"Bearer {tok['access_token']}"
            self._session.client = client
            self._session.expires_at = now + tok.get("expires_in", 300) - 30
            return client

    def search(self, query, satellite, sensor):
        from mosdac_agent.catalog import search_catalogue
        return search_catalogue(query=query, satellite=satellite, sensor=sensor)

    def place_order(self, payload: dict, idempotency_key: str) -> dict:
        client = self._login()
        try:
            r = client.post(
                "/api/v1/orders",
                json=payload,
                headers={"Idempotency-Key": idempotency_key},
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"MOSDAC network error: {exc}") from exc
        if r.status_code not in (200, 201, 202):
            raise UpstreamError(
                f"MOSDAC order rejected: {r.status_code} {r.text[:200]}"
            )
        body = r.json()
        order_id = body.get("orderId") or body.get("order_id") or body.get("requestId")
        if not order_id:
            raise UpstreamError(f"MOSDAC response missing order id: {body}")
        body.setdefault("order_id", order_id)
        return body

    def get_order(self, order_id: str) -> dict:
        client = self._login()
        try:
            r = client.get(f"/api/v1/orders/{order_id}")
        except httpx.HTTPError as exc:
            raise UpstreamError(f"MOSDAC network error: {exc}") from exc
        if r.status_code == 404:
            raise NotFoundError(f"Order {order_id} not found.")
        if r.status_code != 200:
            raise UpstreamError(f"MOSDAC error {r.status_code}: {r.text[:200]}")
        return r.json()


@dataclass
class _MockOrder:
    order_id: str
    payload: dict
    created_at: float = field(default_factory=time.time)
    progression: List[str] = field(
        default_factory=lambda: [
            "queued", "slicing", "packaging", "ready", "notified",
        ]
    )

    def status_now(self) -> str:
        elapsed = time.time() - self.created_at
        idx = min(int(elapsed // 5), len(self.progression) - 1)
        return self.progression[idx]


class MockMosdacClient:
    """Pure-in-process backend. Used by tests and offline dev."""

    def __init__(self) -> None:
        self._orders: Dict[str, _MockOrder] = {}
        self._idem: Dict[str, str] = {}
        self._lock = threading.RLock()

    def search(self, query, satellite, sensor):
        from mosdac_agent.catalog import search_catalogue
        return search_catalogue(query=query, satellite=satellite, sensor=sensor)

    def place_order(self, payload: dict, idempotency_key: str) -> dict:
        with self._lock:
            if idempotency_key in self._idem:
                return self._format(self._orders[self._idem[idempotency_key]], duplicate=True)
            order_id = f"MOCK-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
            self._orders[order_id] = _MockOrder(order_id=order_id, payload=dict(payload))
            self._idem[idempotency_key] = order_id
            return self._format(self._orders[order_id], duplicate=False)

    def get_order(self, order_id: str) -> dict:
        with self._lock:
            if order_id not in self._orders:
                raise NotFoundError(f"Order {order_id} not found.")
            return self._format(self._orders[order_id])

    def _format(self, order: _MockOrder, duplicate: bool = False) -> dict:
        status = order.status_now()
        return {
            "order_id": order.order_id,
            "status": status,
            "duplicate": duplicate,
            "progress": min(100, int((time.time() - order.created_at) * 5)),
            "sftp_path": f"{mosdac_settings.sftp_base_url}/{order.order_id}/",
            "files_ready": 0 if status in ("queued", "slicing") else 1,
            "eta": (
                datetime.utcfromtimestamp(order.created_at) + timedelta(hours=2)
            ).isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }


def build_default_client(settings: Optional[MosdacSettings] = None) -> MosdacClient:
    s = settings or mosdac_settings
    if s.mosdac_use_mock:
        return MockMosdacClient()
    return HttpMosdacClient(s)
