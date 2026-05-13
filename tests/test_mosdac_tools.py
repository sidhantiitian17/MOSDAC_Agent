"""Unit tests for the pure-Python tool layer in mosdac_agent/tools.py.

These tests run entirely in-process: no Ollama, no MCP server, no Neo4j.
"""
from __future__ import annotations

import pytest

from mosdac_agent.client import MockMosdacClient
from mosdac_agent.config import MosdacSettings
from mosdac_agent.exceptions import RateLimitError, ValidationError
from mosdac_agent.store import InMemoryStore, SqliteStore
from mosdac_agent.tools import (
    ToolContext,
    check_order_status_impl,
    list_my_orders_impl,
    place_order_impl,
    search_products_impl,
)


def _ctx(**overrides) -> ToolContext:
    return ToolContext(
        user=overrides.get("user", "alice"),
        store=overrides.get("store", InMemoryStore()),
        client=overrides.get("client", MockMosdacClient()),
        settings=overrides.get(
            "settings",
            MosdacSettings(
                _env_file=None,
                mosdac_use_mock=True,
                max_orders_per_user_per_hour=3,
                max_files_per_order=200,
            ),
        ),
    )


def test_search_products_returns_insat_3d_l1b():
    ctx = _ctx()
    rows = search_products_impl(ctx, query="INSAT-3D", satellite="INSAT-3D")
    ids = {r["dataset_id"] for r in rows}
    assert "3SIMG_L1B_STD" in ids


def test_search_products_finds_tir_band_via_keyword():
    ctx = _ctx()
    rows = search_products_impl(ctx, query="TIR-1")
    ids = {r["dataset_id"] for r in rows}
    assert "3SIMG_L1B_STD" in ids  # has TIR-1 in its bands


def test_search_products_filters_by_satellite():
    ctx = _ctx()
    rows = search_products_impl(ctx, query="", satellite="SCATSAT-1")
    assert all("SCATSAT" in r["satellite"] for r in rows)


def test_place_order_rejects_bad_date_format():
    ctx = _ctx()
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        place_order_impl(
            ctx,
            dataset_id="3SIMG_L1B_STD",
            start_date="14-08-2024",
            end_date="2024-08-18",
            state_or_region="Tamil Nadu",
        )


def test_place_order_rejects_end_before_start():
    ctx = _ctx()
    with pytest.raises(ValidationError, match="end_date"):
        place_order_impl(
            ctx,
            dataset_id="3SIMG_L1B_STD",
            start_date="2024-08-18",
            end_date="2024-08-14",
            state_or_region="Tamil Nadu",
        )


def test_place_order_rejects_oversized_range():
    ctx = _ctx()
    with pytest.raises(ValidationError, match="Maximum date range"):
        place_order_impl(
            ctx,
            dataset_id="3SIMG_L1B_STD",
            start_date="2024-01-01",
            end_date="2024-08-18",
            state_or_region="Tamil Nadu",
        )


def test_place_order_requires_aoi():
    ctx = _ctx()
    with pytest.raises(ValidationError, match="bounding_box or state_or_region"):
        place_order_impl(
            ctx,
            dataset_id="3SIMG_L1B_STD",
            start_date="2024-08-14",
            end_date="2024-08-18",
        )


def test_place_order_rejects_unknown_region():
    ctx = _ctx()
    with pytest.raises(ValidationError, match="Unknown region"):
        place_order_impl(
            ctx,
            dataset_id="3SIMG_L1B_STD",
            start_date="2024-08-14",
            end_date="2024-08-18",
            state_or_region="Atlantis",
        )


def test_place_order_rejects_unknown_dataset():
    ctx = _ctx()
    with pytest.raises(ValidationError, match="Unknown dataset_id"):
        place_order_impl(
            ctx,
            dataset_id="FOO_BAR_BAZ",
            start_date="2024-08-14",
            end_date="2024-08-18",
            state_or_region="Tamil Nadu",
        )


def test_place_order_rejects_oversized_max_files():
    ctx = _ctx()
    with pytest.raises(ValidationError, match="max_files"):
        place_order_impl(
            ctx,
            dataset_id="3SIMG_L1B_STD",
            start_date="2024-08-14",
            end_date="2024-08-18",
            state_or_region="Tamil Nadu",
            max_files=10_000,
        )


def test_place_order_rejects_non_positive_max_files():
    ctx = _ctx()
    with pytest.raises(ValidationError, match="max_files"):
        place_order_impl(
            ctx,
            dataset_id="3SIMG_L1B_STD",
            start_date="2024-08-14",
            end_date="2024-08-18",
            state_or_region="Tamil Nadu",
            max_files=0,
        )


def test_place_order_rejects_invalid_level():
    ctx = _ctx()
    with pytest.raises(ValidationError, match="level_format"):
        place_order_impl(
            ctx,
            dataset_id="3SIMG_L1B_STD",
            start_date="2024-08-14",
            end_date="2024-08-18",
            state_or_region="Tamil Nadu",
            level_format="BOGUS",
        )


def test_place_order_happy_path_returns_order_id_and_sftp_path():
    ctx = _ctx()
    out = place_order_impl(
        ctx,
        dataset_id="3SIMG_L1B_STD",
        start_date="2024-08-14",
        end_date="2024-08-18",
        state_or_region="Tamil Nadu",
    )
    assert out["order_id"].startswith("MOCK-")
    assert out["dataset_id"] == "3SIMG_L1B_STD"
    assert out["bounding_box"] == "76.2,8.0,80.4,13.6"
    assert out["delivery"] == "SFTP"
    assert out["sftp_path"].startswith("sftp://")


def test_place_order_records_audit_row():
    store = InMemoryStore()
    ctx = _ctx(store=store)
    place_order_impl(
        ctx,
        dataset_id="3SIMG_L1B_STD",
        start_date="2024-08-14",
        end_date="2024-08-18",
        state_or_region="Tamil Nadu",
    )
    rows = store.list_orders("alice", limit=5)
    assert len(rows) == 1
    assert rows[0]["payload"]["datasetId"] == "3SIMG_L1B_STD"


def test_place_order_is_idempotent_for_same_key():
    ctx = _ctx()
    first = place_order_impl(
        ctx,
        dataset_id="3SIMG_L1B_STD",
        start_date="2024-08-14",
        end_date="2024-08-18",
        state_or_region="Tamil Nadu",
        idempotency_key="user-supplied-1",
    )
    second = place_order_impl(
        ctx,
        dataset_id="3SIMG_L1B_STD",
        start_date="2024-08-14",
        end_date="2024-08-18",
        state_or_region="Tamil Nadu",
        idempotency_key="user-supplied-1",
    )
    assert first["order_id"] == second["order_id"]
    assert second["status"] == "duplicate"


def test_place_order_rate_limit_per_user_per_hour():
    ctx = _ctx()
    for _ in range(3):
        place_order_impl(
            ctx,
            dataset_id="3SIMG_L1B_STD",
            start_date="2024-08-14",
            end_date="2024-08-18",
            state_or_region="Tamil Nadu",
        )
    with pytest.raises(RateLimitError):
        place_order_impl(
            ctx,
            dataset_id="3SIMG_L1B_STD",
            start_date="2024-08-14",
            end_date="2024-08-18",
            state_or_region="Tamil Nadu",
        )


def test_check_order_status_returns_order_state():
    ctx = _ctx()
    placed = place_order_impl(
        ctx,
        dataset_id="3SIMG_L1B_STD",
        start_date="2024-08-14",
        end_date="2024-08-18",
        state_or_region="Tamil Nadu",
    )
    status = check_order_status_impl(ctx, order_id=placed["order_id"])
    assert status["order_id"] == placed["order_id"]
    assert status["status"] in {"queued", "slicing", "packaging", "ready", "notified"}


def test_check_order_status_rejects_empty_id():
    ctx = _ctx()
    with pytest.raises(ValidationError):
        check_order_status_impl(ctx, order_id="")


def test_list_my_orders_returns_recent_orders():
    ctx = _ctx()
    place_order_impl(
        ctx,
        dataset_id="3SIMG_L1B_STD",
        start_date="2024-08-14",
        end_date="2024-08-18",
        state_or_region="Tamil Nadu",
    )
    place_order_impl(
        ctx,
        dataset_id="3RIMG_L1B_STD",
        start_date="2024-09-01",
        end_date="2024-09-05",
        state_or_region="Kerala",
    )
    rows = list_my_orders_impl(ctx, limit=10)
    assert len(rows) == 2


def test_list_my_orders_isolates_users():
    store = InMemoryStore()
    client = MockMosdacClient()
    settings = MosdacSettings(
        _env_file=None, mosdac_use_mock=True, max_files_per_order=200,
    )
    ctx_a = ToolContext(user="alice", store=store, client=client, settings=settings)
    ctx_b = ToolContext(user="bob", store=store, client=client, settings=settings)
    place_order_impl(
        ctx_a,
        dataset_id="3SIMG_L1B_STD",
        start_date="2024-08-14",
        end_date="2024-08-18",
        state_or_region="Tamil Nadu",
    )
    assert len(list_my_orders_impl(ctx_a, limit=5)) == 1
    assert len(list_my_orders_impl(ctx_b, limit=5)) == 0


def test_sqlite_store_persists_idempotency(tmp_path):
    store = SqliteStore(path=tmp_path / "idem.sqlite")
    store.save_idempotent("k1", "order-1")
    assert store.find_idempotent("k1") == "order-1"
    store.save_idempotent("k1", "order-2")  # no-op
    assert store.find_idempotent("k1") == "order-1"


def test_sqlite_store_counts_orders_in_last_hour(tmp_path):
    store = SqliteStore(path=tmp_path / "idem.sqlite")
    store.record_order("u", "o1", {"datasetId": "X"})
    store.record_order("u", "o2", {"datasetId": "Y"})
    assert store.orders_in_last_hour("u") == 2
    assert store.orders_in_last_hour("other") == 0
