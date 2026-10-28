import httpx
import pytest
from bs4 import BeautifulSoup

from cents_pipeline.rate_limiter import HostRateLimiter
from cents_pipeline.scraper import PoliteFetcher, RobotsCache, fetch_dom

FAST = dict(rate=1000)


def fetcher(handler, limiter=None, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PoliteFetcher(client, limiter or HostRateLimiter(**FAST), max_retries=1, **kw), client


def test_robots_policy():
    r = RobotsCache("CENTS-Audit")
    r.load("https://a.test", 200, "User-agent: *\nDisallow: /private")
    r.load("https://b.test", 404)
    r.load("https://c.test", 500)
    r.load("https://d.test", None)
    r.load("https://e.test", 403)
    assert r.allowed("https://a.test/x") and not r.allowed("https://a.test/private/x")
    assert r.allowed("https://b.test/x")
    assert not any(r.allowed(f"https://{h}.test/x") for h in "cde")  # server error / network error / forbidden: stay out


def test_robots_unknown_origin_is_not_allowed():
    assert not RobotsCache("x").allowed("https://never-fetched.test/")


async def test_user_agent_robots_once_per_origin_and_disallow():
    seen = []

    def handler(req):
        seen.append((req.url.path, req.headers["user-agent"]))
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret")
        return httpx.Response(200, text="hi")

    f, client = fetcher(handler)
    async with client:
        assert await f.get("https://x.test/secret") is None
        assert (await f.get("https://x.test/a")).text == "hi"
        assert (await f.get("https://x.test/b")).text == "hi"
    assert [p for p, _ in seen].count("/robots.txt") == 1
    assert all(ua.startswith("CENTS-Audit/") for _, ua in seen)
    assert f.stats["blocked_by_robots"] == 1


async def test_retry_after_on_429_then_success():
    calls = {"n": 0}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(429 if calls["n"] == 1 else 200, headers={"Retry-After": "0"}, text="ok")

    f, client = fetcher(handler)
    async with client:
        assert (await f.get("https://x.test/p")).text == "ok"
    assert f.stats["throttled"] == 1


async def test_network_error_returns_none():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError("down")

    f, client = fetcher(handler)
    async with client:
        assert await f.get("https://x.test/p") is None
    assert f.stats["errors"] >= 1


async def test_every_request_including_robots_is_rate_limited():
    t = {"now": 0.0}
    stamps = []

    async def sleep(s):
        t["now"] += s

    lim = HostRateLimiter(clock=lambda: t["now"], sleep=sleep)

    def handler(req):
        stamps.append(t["now"])
        return httpx.Response(404 if "robots" in req.url.path else 200, text="x")

    f, client = fetcher(handler, lim)
    import asyncio
    async with client:
        await asyncio.gather(*(f.get(f"https://h.test/p{i}") for i in range(4)))
    assert len(stamps) == 5  # robots.txt + 4 pages
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(stamps, stamps[1:]))


async def test_fetch_dom_parses_html_and_returns_none_on_errors():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/gone":
            return httpx.Response(404)
        return httpx.Response(200, text="<html><body><h1 id='t'>Hello</h1></body></html>")

    f, client = fetcher(handler)
    async with client:
        dom = await fetch_dom(f, "https://x.test/page")
        assert isinstance(dom, BeautifulSoup) and dom.select_one("#t").get_text() == "Hello"
        assert await fetch_dom(f, "https://x.test/gone") is None


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_robots_failure_blocks_everything(status):
    f, client = fetcher(lambda req: httpx.Response(status, text="no"))
    async with client:
        assert await f.get("https://x.test/p") is None
