"""Per-host async token bucket. Default: 1 request/second/host, no bursting."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse


class _Bucket:
    __slots__ = ("lock", "stamp", "tokens")

    def __init__(self, capacity: float, now: float) -> None:
        self.tokens = capacity
        self.stamp = now
        self.lock = asyncio.Lock()


class HostRateLimiter:
    """Token bucket per host. `acquire(host)` returns once a request to `host` is allowed.

    Requests to different hosts never wait on each other; requests to the same host
    are spaced at least 1/rate seconds apart (with burst=1).
    """

    def __init__(
        self,
        rate: float = 1.0,
        burst: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be > 0 and burst >= 1")
        self.rate, self.burst = rate, float(burst)
        self._clock, self._sleep = clock, sleep
        self._buckets: dict[str, _Bucket] = {}
        self._rates: dict[str, float] = {}

    def set_min_interval(self, host: str, seconds: float) -> None:
        """Slow a host down further (e.g. robots.txt Crawl-delay). Never speeds it up past `rate`."""
        if seconds > 0:
            self._rates[host.lower()] = min(self.rate, 1.0 / seconds)

    async def acquire(self, host: str) -> None:
        host = host.lower()
        bucket = self._buckets.get(host)
        if bucket is None:
            bucket = self._buckets[host] = _Bucket(self.burst, self._clock())
        rate = self._rates.get(host, self.rate)
        async with bucket.lock:  # FIFO per host: the lock queues waiters in order
            while True:
                now = self._clock()
                bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.stamp) * rate)
                bucket.stamp = now
                if bucket.tokens >= 1:
                    bucket.tokens -= 1
                    return
                await self._sleep((1 - bucket.tokens) / rate)

    async def wait_for_host(self, url: str) -> None:
        """Block until a request to `url`'s host is allowed (host = scheme-less netloc, lowercased)."""
        host = urlparse(url).netloc
        if not host:
            raise ValueError(f"url has no host: {url!r}")
        await self.acquire(host)

