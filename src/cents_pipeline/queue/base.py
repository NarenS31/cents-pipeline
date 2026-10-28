"""Work-queue seam for scaling out. Only the in-process queue is implemented.

Design for Redis/Celery workers is in docs/DISTRIBUTED.md; a distributed backend would implement
TaskQueue and must keep each registrable host on one worker so the 1 req/sec/host limit holds.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol, TypeVar
from urllib.parse import urlsplit

from ..models import DistrictRecord

T = TypeVar("T")


def partition_by_host(districts: Iterable[DistrictRecord], n_batches: int) -> list[list[DistrictRecord]]:
    """Split districts into batches such that all districts sharing a host land in one batch."""
    by_host: dict[str, list[DistrictRecord]] = defaultdict(list)
    for d in districts:
        by_host[urlsplit(d.website or "").netloc or f"nohost:{d.id}"].append(d)
    batches: list[list[DistrictRecord]] = [[] for _ in range(max(1, n_batches))]
    for group in sorted(by_host.values(), key=len, reverse=True):
        min(batches, key=len).extend(group)  # greedy: fill the smallest batch
    return [b for b in batches if b]


class TaskQueue(Protocol):
    async def map_batches(self, batches: list[list[DistrictRecord]],
                          fn: Callable[[list[DistrictRecord]], Awaitable[T]]) -> list[T]: ...


class LocalQueue:
    """Runs batches concurrently in this process."""

    async def map_batches(self, batches, fn):
        import asyncio

        return list(await asyncio.gather(*(fn(b) for b in batches)))
