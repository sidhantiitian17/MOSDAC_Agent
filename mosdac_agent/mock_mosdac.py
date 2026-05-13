"""Standalone fake MOSDAC backend (for offline integration tests).

Run:
    python -m mosdac_agent.mock_mosdac
    # or
    uvicorn mosdac_agent.mock_mosdac:app --host 0.0.0.0 --port 9000
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock MOSDAC Order API")

_orders: Dict[str, dict] = {}
_idem: Dict[str, str] = {}
_lock = threading.RLock()


class OrderPayload(BaseModel):
    datasetId: str
    startTime: str
    endTime: str
    boundingBox: str
    count: int = 100
    level_format: str = "L1B_HDF5"
    delivery: str = "SFTP"


@app.post("/auth/realms/Mosdac/protocol/openid-connect/token")
def fake_token():
    return {
        "access_token": "fake-token-" + uuid.uuid4().hex[:8],
        "expires_in": 300,
        "token_type": "Bearer",
    }


@app.post("/api/v1/orders")
def create_order(
    body: OrderPayload,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    key = idempotency_key or uuid.uuid4().hex
    with _lock:
        if key in _idem:
            return _orders[_idem[key]]
        order_id = f"FAKE-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        record = {
            "order_id": order_id,
            "orderId": order_id,
            "status": "queued",
            "eta": (datetime.utcnow() + timedelta(hours=2)).isoformat() + "Z",
            "sftp_path": f"sftp://ftp.mosdac.gov.in/{order_id}/",
            "payload": body.model_dump(),
            "created_at": time.time(),
        }
        _orders[order_id] = record
        _idem[key] = order_id
        return record


@app.get("/api/v1/orders/{order_id}")
def get_order(order_id: str):
    with _lock:
        if order_id not in _orders:
            raise HTTPException(status_code=404, detail="not found")
        order = dict(_orders[order_id])
    elapsed = time.time() - order["created_at"]
    progression = ["queued", "slicing", "packaging", "ready", "notified"]
    idx = min(int(elapsed // 5), len(progression) - 1)
    order["status"] = progression[idx]
    order["files_ready"] = 1 if idx >= 3 else 0
    return order


@app.get("/health")
def health():
    return {"ok": True, "service": "mock-mosdac"}


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "mosdac_agent.mock_mosdac:app",
        host="0.0.0.0",
        port=9000,
        log_level="info",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
