"""Pure-Python implementations of the four MOSDAC agent tools.

Layering:
  * `*_impl(ctx, ...)` are testable without LangChain or MCP.
  * `build_local_tools(ctx)` returns LangChain `StructuredTool`s.
  * `mcp_server.py` registers the same `_impl` callables as FastMCP tools.

`ToolContext` carries per-request dependencies (user, store, client, settings)
so the package is safely importable from any host process — no globals.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from mosdac_agent.catalog import resolve_region, search_catalogue
from mosdac_agent.client import MosdacClient
from mosdac_agent.config import MosdacSettings, mosdac_settings
from mosdac_agent.exceptions import RateLimitError, ValidationError
from mosdac_agent.store import Store


@dataclass
class ToolContext:
    """Everything a tool needs at call-time, injected from the outside."""

    user: str
    store: Store
    client: MosdacClient
    settings: MosdacSettings = field(default_factory=lambda: mosdac_settings)


def search_products_impl(
    ctx: ToolContext,
    query: str = "",
    satellite: Optional[str] = None,
    sensor: Optional[str] = None,
) -> List[dict]:
    """Resolve a free-form product description to dataset_id rows."""
    return ctx.client.search(query=query, satellite=satellite, sensor=sensor)


def place_order_impl(
    ctx: ToolContext,
    dataset_id: str,
    start_date: str,
    end_date: str,
    bounding_box: Optional[str] = None,
    state_or_region: Optional[str] = None,
    level_format: str = "L1B_HDF5",
    delivery: str = "SFTP",
    idempotency_key: Optional[str] = None,
    max_files: int = 100,
) -> dict:
    """Place a satellite-data order on behalf of ctx.user."""
    settings = ctx.settings
    valid_levels = {"L1B_HDF5", "L1C_HDF5", "L2_NetCDF", "L3_CSV"}
    valid_delivery = {"SFTP", "HTTP", "EMAIL"}
    if level_format not in valid_levels:
        raise ValidationError(f"level_format must be one of {sorted(valid_levels)}.")
    if delivery not in valid_delivery:
        raise ValidationError(f"delivery must be one of {sorted(valid_delivery)}.")

    try:
        d0 = datetime.strptime(start_date, "%Y-%m-%d")
        d1 = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValidationError("Dates must be in YYYY-MM-DD format.") from exc
    if d1 < d0:
        raise ValidationError("end_date is before start_date.")
    if (d1 - d0).days > settings.max_date_range_days:
        raise ValidationError(
            f"Maximum date range is {settings.max_date_range_days} days per order."
        )

    if max_files <= 0:
        raise ValidationError("max_files must be positive.")
    if max_files > settings.max_files_per_order:
        raise ValidationError(
            f"max_files {max_files} exceeds server cap "
            f"{settings.max_files_per_order}."
        )

    if not bounding_box and not state_or_region:
        raise ValidationError("Provide either bounding_box or state_or_region.")

    if not any(r["dataset_id"] == dataset_id for r in search_catalogue("")):
        raise ValidationError(
            f"Unknown dataset_id '{dataset_id}'. "
            "Call search_products first to find a valid identifier."
        )

    if not bounding_box:
        bbox = resolve_region(state_or_region or "")
        if not bbox:
            raise ValidationError(
                f"Unknown region '{state_or_region}'. "
                "Provide bounding_box (minLon,minLat,maxLon,maxLat) instead."
            )
        bounding_box = bbox

    idem = idempotency_key or str(uuid.uuid4())
    prev = ctx.store.find_idempotent(idem)
    if prev:
        existing = ctx.client.get_order(prev)
        existing["status"] = "duplicate"
        existing["message"] = (
            "Idempotency-Key already used; returning the original order."
        )
        existing["idempotency_key"] = idem
        return existing

    if ctx.store.orders_in_last_hour(ctx.user) >= settings.max_orders_per_user_per_hour:
        raise RateLimitError(
            f"Rate limit hit: you have placed "
            f"{settings.max_orders_per_user_per_hour} orders in the last hour."
        )

    payload = {
        "datasetId": dataset_id,
        "startTime": start_date,
        "endTime": end_date,
        "boundingBox": bounding_box,
        "count": max_files,
        "level_format": level_format,
        "delivery": delivery,
    }
    response = ctx.client.place_order(payload, idempotency_key=idem)
    order_id = response.get("order_id") or response.get("orderId")
    if not order_id:
        raise ValidationError(f"Backend did not return an order id: {response}")

    ctx.store.save_idempotent(idem, order_id)
    ctx.store.record_order(ctx.user, order_id, payload)

    return {
        "order_id": order_id,
        "status": response.get("status", "queued"),
        "eta": response.get("eta")
        or (datetime.utcnow() + timedelta(hours=2)).isoformat() + "Z",
        "delivery": delivery,
        "sftp_path": response.get(
            "sftp_path",
            f"{settings.sftp_base_url}/{order_id}/" if delivery == "SFTP" else None,
        ),
        "dataset_id": dataset_id,
        "bounding_box": bounding_box,
        "idempotency_key": idem,
    }


def check_order_status_impl(ctx: ToolContext, order_id: str) -> dict:
    """Look up the live status of an order placed earlier."""
    if not order_id:
        raise ValidationError("order_id is required.")
    return ctx.client.get_order(order_id)


def list_my_orders_impl(ctx: ToolContext, limit: int = 20) -> List[dict]:
    """Return the most recent orders placed by ctx.user."""
    if limit <= 0 or limit > 100:
        raise ValidationError("limit must be between 1 and 100.")
    rows = ctx.store.list_orders(ctx.user, limit=limit)
    return [
        {
            "order_id": row["order_id"],
            "payload": row["payload"],
            "created_at": datetime.utcfromtimestamp(row["created_at"]).isoformat() + "Z",
        }
        for row in rows
    ]


def build_local_tools(ctx: ToolContext):
    """Wrap the impl functions as LangChain `StructuredTool`s.

    The LLM sees these tools directly (no MCP transport). Each tool's JSON
    schema is auto-generated from its Python signature + docstring.
    """
    from langchain_core.tools import StructuredTool

    def _search_products(
        query: str = "",
        satellite: Optional[str] = None,
        sensor: Optional[str] = None,
    ) -> List[dict]:
        """Search the MOSDAC satellite-product catalogue.

        Call this BEFORE place_order if the user describes a product in words
        and you need the exact dataset_id."""
        return search_products_impl(ctx, query=query, satellite=satellite, sensor=sensor)

    def _place_order(
        dataset_id: str,
        start_date: str,
        end_date: str,
        bounding_box: Optional[str] = None,
        state_or_region: Optional[str] = None,
        level_format: str = "L1B_HDF5",
        delivery: str = "SFTP",
        max_files: int = 100,
    ) -> dict:
        """Place a MOSDAC satellite-data order. Dates must be YYYY-MM-DD.

        Provide EITHER bounding_box (minLon,minLat,maxLon,maxLat) OR a
        state_or_region name. Delivery defaults to SFTP."""
        return place_order_impl(
            ctx,
            dataset_id=dataset_id,
            start_date=start_date,
            end_date=end_date,
            bounding_box=bounding_box,
            state_or_region=state_or_region,
            level_format=level_format,
            delivery=delivery,
            max_files=max_files,
        )

    def _check_order_status(order_id: str) -> dict:
        """Poll the status of a previously placed order."""
        return check_order_status_impl(ctx, order_id=order_id)

    def _list_my_orders(limit: int = 20) -> List[dict]:
        """List this user's most recent orders."""
        return list_my_orders_impl(ctx, limit=limit)

    return [
        StructuredTool.from_function(_search_products, name="search_products"),
        StructuredTool.from_function(_place_order, name="place_order"),
        StructuredTool.from_function(_check_order_status, name="check_order_status"),
        StructuredTool.from_function(_list_my_orders, name="list_my_orders"),
    ]
