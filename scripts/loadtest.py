#!/usr/bin/env python3
"""Concurrency load test for the MOSDAC chat API (production.md §4).

Proves the P0-1 latency fix under real concurrency. Self-contained — uses httpx
(already a dependency), no locust/k6 needed.

Usage:
    python scripts/loadtest.py --url http://localhost:8000 \
        --concurrency 50 --requests 500

It fires N requests with a bounded concurrency level against POST /chat using
fresh UUID sessions and a pool of MOSDAC-domain questions, then reports
throughput and p50/p95/p99 latency so you can check it against an SLO
(e.g. "p95 < 3s @ 50 concurrent").
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import time
import uuid

QUESTIONS = [
    "What sensors does INSAT-3D carry?",
    "What is the spatial resolution of Oceansat-2 OCM?",
    "How do I download MOSDAC rainfall data?",
    "What is the swath width of SCATSAT-1?",
    "Tell me about INSAT-3DR IMAGER channels.",
    "What ocean colour products does MOSDAC provide?",
    "What is sea surface temperature retrieval?",
    "Explain the cyclone tracking products of MOSDAC.",
]


async def _one(client, url: str, i: int) -> tuple[int, float]:
    payload = {"session_id": str(uuid.uuid4()), "message": QUESTIONS[i % len(QUESTIONS)]}
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{url}/chat", json=payload, timeout=120)
        status = r.status_code
    except Exception:
        status = 0
    return status, (time.perf_counter() - t0) * 1000.0


async def _run(url: str, concurrency: int, total: int, api_key: str | None) -> None:
    import httpx

    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    headers = {"X-API-Key": api_key} if api_key else {}

    async with httpx.AsyncClient(headers=headers) as client:
        async def worker(i: int):
            async with sem:
                status, ms = await _one(client, url, i)
                latencies.append(ms)
                statuses[status] = statuses.get(status, 0) + 1

        wall0 = time.perf_counter()
        await asyncio.gather(*(worker(i) for i in range(total)))
        wall = time.perf_counter() - wall0

    latencies.sort()

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(len(latencies) - 1, int(round(p / 100 * len(latencies))) - 1)
        return latencies[max(0, idx)]

    print(f"\nRequests:        {total} @ concurrency {concurrency}")
    print(f"Wall time:       {wall:.2f}s")
    print(f"Throughput:      {total / wall:.1f} req/s")
    print(f"Status codes:    {statuses}")
    if latencies:
        print(f"Latency p50:     {statistics.median(latencies):.0f} ms")
        print(f"Latency p95:     {pct(95):.0f} ms")
        print(f"Latency p99:     {pct(99):.0f} ms")
        print(f"Latency max:     {latencies[-1]:.0f} ms")


def main() -> None:
    ap = argparse.ArgumentParser(description="MOSDAC chat API load test")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--requests", type=int, default=500)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()
    asyncio.run(_run(args.url, args.concurrency, args.requests, args.api_key))


if __name__ == "__main__":
    main()
