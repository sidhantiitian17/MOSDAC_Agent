"""FastMCP server exposing the four MOSDAC tools.

Run locally (stdio — for MCP Inspector / Claude Desktop):
    python -m mosdac_agent.mcp_server

Run as remote HTTP server (for production agents):
    MCP_TRANSPORT=streamable-http python -m mosdac_agent.mcp_server
"""
from __future__ import annotations

import logging
from typing import Optional

from mosdac_agent.client import MosdacClient, build_default_client
from mosdac_agent.config import MosdacSettings, mosdac_settings
from mosdac_agent.exceptions import MosdacError
from mosdac_agent.store import Store, build_default_store
from mosdac_agent.tools import (
    ToolContext,
    check_order_status_impl,
    list_my_orders_impl,
    place_order_impl,
    search_products_impl,
)

log = logging.getLogger("mosdac-mcp")


def build_mcp_server(
    *,
    settings: Optional[MosdacSettings] = None,
    store: Optional[Store] = None,
    client: Optional[MosdacClient] = None,
    default_user: str = "default",
):
    """Construct a FastMCP server with the four MOSDAC tools.

    Dependencies are injected so the same server can be wired against either
    a real MOSDAC backend or the in-process mock without touching code.
    """
    try:
        from fastmcp import FastMCP
        from fastmcp.exceptions import ToolError
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "fastmcp is required to run the MCP server. "
            "Install it with: pip install fastmcp"
        ) from exc

    s = settings or mosdac_settings
    st = store or build_default_store()
    cl = client or build_default_client(s)
    ctx = ToolContext(user=default_user, store=st, client=cl, settings=s)

    mcp = FastMCP(name=s.mcp_server_name)

    def _wrap(fn):
        def _runner(*args, **kwargs):
            try:
                return fn(ctx, *args, **kwargs)
            except MosdacError as exc:
                raise ToolError(str(exc)) from exc

        _runner.__doc__ = fn.__doc__
        return _runner

    @mcp.tool
    def search_products(
        query: str = "",
        satellite: Optional[str] = None,
        sensor: Optional[str] = None,
    ) -> list:
        """Search the MOSDAC satellite-product catalogue.

        Call this BEFORE place_order if the user describes a product in words
        (e.g. 'INSAT-3D TIR-1 L1B') and you need the exact dataset_id."""
        return _wrap(search_products_impl)(
            query=query, satellite=satellite, sensor=sensor
        )

    @mcp.tool
    def place_order(
        dataset_id: str,
        start_date: str,
        end_date: str,
        bounding_box: Optional[str] = None,
        state_or_region: Optional[str] = None,
        level_format: str = "L1B_HDF5",
        delivery: str = "SFTP",
        max_files: int = 100,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Place a MOSDAC satellite-data order on behalf of the authenticated user."""
        return _wrap(place_order_impl)(
            dataset_id=dataset_id,
            start_date=start_date,
            end_date=end_date,
            bounding_box=bounding_box,
            state_or_region=state_or_region,
            level_format=level_format,
            delivery=delivery,
            max_files=max_files,
            idempotency_key=idempotency_key,
        )

    @mcp.tool
    def check_order_status(order_id: str) -> dict:
        """Poll the status of a previously placed order."""
        return _wrap(check_order_status_impl)(order_id=order_id)

    @mcp.tool
    def list_my_orders(limit: int = 20) -> list:
        """List the most recent orders this server has placed (audit view)."""
        return _wrap(list_my_orders_impl)(limit=limit)

    return mcp


def main() -> None:  # pragma: no cover
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    mcp = build_mcp_server()
    s = mosdac_settings
    if s.mcp_transport == "streamable-http":
        log.info("Starting MCP server on http://%s:%s/mcp/", s.mcp_host, s.mcp_port)
        mcp.run(transport="streamable-http", host=s.mcp_host, port=s.mcp_port)
    else:
        log.info("Starting MCP server on stdio")
        mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
