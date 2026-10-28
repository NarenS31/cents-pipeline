"""Polite async scraper: custom User-Agent, robots.txt evaluation, per-host rate limit, bounded retries."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from .rate_limiter import HostRateLimiter

DEFAULT_UA = "CENTS-Audit/0.1 (+https://centscampaign.org; public-records research; contact via site)"


class RobotsCache:
    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}  # None = disallow all

    @staticmethod
    def origin(url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    def known(self, url: str) -> bool:
        return self.origin(url) in self._parsers

    def load(self, origin: str, status: int | None, body: str = "") -> None:
        """Record the result of fetching `origin/robots.txt` (status None = network error)."""
        if status is None or status >= 500 or status in (401, 403):
            self._parsers[origin] = None
        elif status >= 400:
            rp = RobotFileParser(); rp.parse([]); self._parsers[origin] = rp
        else:
            rp = RobotFileParser(); rp.parse(body.splitlines()); self._parsers[origin] = rp

    def allowed(self, url: str) -> bool:
        rp = self._parsers.get(self.origin(url))
        return bool(rp and rp.can_fetch(self.user_agent, url))

    def crawl_delay(self, url: str) -> float | None:
        rp = self._parsers.get(self.origin(url))
        d = rp.crawl_delay(self.user_agent) if rp else None
        return float(d) if d else None


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    text: str


class PoliteFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        limiter: HostRateLimiter | None = None,
        user_agent: str | None = None,
        max_retries: int = 2,
        max_concurrency: int = 16,
        max_retry_after: float = 30.0,
    ) -> None:
        self.client = client
        self.limiter = limiter or HostRateLimiter()
        self.user_agent = user_agent or os.environ.get("CENTS_USER_AGENT", DEFAULT_UA)
        self.robots = RobotsCache(self.user_agent)
        self.max_retries, self.max_retry_after = max_retries, max_retry_after
        self._sem = asyncio.Semaphore(max_concurrency)
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self.stats = {"requests": 0, "blocked_by_robots": 0, "throttled": 0, "errors": 0}

    async def _request(self, url: str) -> httpx.Response | None:
        host = urlsplit(url).netloc
        for attempt in range(self.max_retries + 1):
            await self.limiter.acquire(host)  # every request, including robots.txt and retries
            self.stats["requests"] += 1
            try:
                async with self._sem:
                    resp = await self.client.get(url, headers={"User-Agent": self.user_agent}, follow_redirects=True)
            except httpx.HTTPError:
                self.stats["errors"] += 1
                continue
            if resp.status_code in (429, 503) and attempt < self.max_retries:
                self.stats["throttled"] += 1
                try:
                    wait = float(resp.headers.get("Retry-After", "2"))
                except ValueError:
                    wait = 2.0
                await asyncio.sleep(min(wait, self.max_retry_after))
                continue
            return resp
        return None

    async def _ensure_robots(self, url: str) -> None:
        origin = RobotsCache.origin(url)
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if self.robots.known(url):
                return
            resp = await self._request(origin + "/robots.txt")
            self.robots.load(origin, resp.status_code if resp else None, resp.text if resp else "")
            delay = self.robots.crawl_delay(origin + "/")
            if delay:
                self.limiter.set_min_interval(urlsplit(origin).netloc, delay)

    async def get(self, url: str) -> FetchResult | None:
        """Fetch `url` if robots.txt allows it. Returns None if blocked or unreachable."""
        await self._ensure_robots(url)
        if not self.robots.allowed(url):
            self.stats["blocked_by_robots"] += 1
            return None
        resp = await self._request(url)
        if resp is None:
            return None
        return FetchResult(url=str(resp.url), status=resp.status_code, text=resp.text if resp.status_code < 400 else "")


async def fetch_dom(fetcher: PoliteFetcher, url: str) -> BeautifulSoup | None:
    """Fetch `url` politely and return its parsed DOM, or None if blocked, failed, or non-2xx/3xx."""
    result = await fetcher.get(url)
    if result is None or result.status >= 400:
        return None
    return BeautifulSoup(result.text, "html.parser")
