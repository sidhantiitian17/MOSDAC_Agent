"""Smoke tests for the standalone fake MOSDAC backend."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from mosdac_agent.mock_mosdac import app
    return TestClient(app)


def test_token_endpoint_returns_access_token(client):
    r = client.post("/auth/realms/Mosdac/protocol/openid-connect/token", data={})
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert body["token_type"] == "Bearer"


def test_create_order_returns_order_id(client):
    r = client.post(
        "/api/v1/orders",
        json={
            "datasetId": "3SIMG_L1B_STD",
            "startTime": "2024-08-14",
            "endTime": "2024-08-18",
            "boundingBox": "76.2,8.0,80.4,13.6",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["order_id"].startswith("FAKE-")
    assert body["status"] == "queued"


def test_get_order_returns_404_for_unknown_id(client):
    r = client.get("/api/v1/orders/does-not-exist")
    assert r.status_code == 404


def test_idempotency_header_replays_same_order(client):
    payload = {
        "datasetId": "3SIMG_L1B_STD",
        "startTime": "2024-08-14",
        "endTime": "2024-08-18",
        "boundingBox": "76.2,8.0,80.4,13.6",
    }
    headers = {"Idempotency-Key": "the-same-key"}
    r1 = client.post("/api/v1/orders", json=payload, headers=headers)
    r2 = client.post("/api/v1/orders", json=payload, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["order_id"] == r2.json()["order_id"]


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
