import asyncio

import pytest

from cents_pipeline.rate_limiter import HostRateLimiter


class FakeTime:
    """Virtual clock: sleeping advances time instantly, so tests are fast and exact."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    async def sleep(self, s):
        self.t += s


@pytest.fixture
def ft():
    return FakeTime()


def make(ft, **kw):
    return HostRateLimiter(clock=ft.now, sleep=ft.sleep, **kw)


async def test_same_host_requests_are_one_second_apart(ft):
    lim, stamps = make(ft), []
    for _ in range(4):
        await lim.wait_for_host("https://a.test/page")
        stamps.append(ft.t)
    assert [round(b - a, 6) for a, b in zip(stamps, stamps[1:])] == [1.0, 1.0, 1.0]


async def test_first_request_per_host_is_immediate_and_hosts_are_independent(ft):
    lim = make(ft)
    for url in ("https://a.test/", "https://b.test/", "https://c.test/x"):
        await lim.wait_for_host(url)
    assert ft.t == 0


async def test_host_key_ignores_path_scheme_case_but_not_subdomain(ft):
    lim = make(ft)
    await lim.wait_for_host("https://A.test/one")
    await lim.wait_for_host("http://a.test/two")  # same netloc, different scheme/case: shares a bucket
    assert ft.t == pytest.approx(1.0)
    await lim.wait_for_host("https://www.a.test/")  # different host: no wait
    assert ft.t == pytest.approx(1.0)


async def test_concurrent_waiters_on_one_host_are_serialised(ft):
    lim, times = make(ft), []

    async def one():
        await lim.wait_for_host("https://a.test/")
        times.append(ft.t)

    await asyncio.gather(*(one() for _ in range(4)))
    times.sort()
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(times, times[1:]))
    assert times[-1] == pytest.approx(3.0)


async def test_custom_rate_and_burst(ft):
    lim = make(ft, rate=2.0, burst=2)
    for _ in range(2):
        await lim.wait_for_host("https://a.test/")
    assert ft.t == 0  # burst of 2 is free
    await lim.wait_for_host("https://a.test/")
    assert ft.t == pytest.approx(0.5)  # then 2 req/sec


async def test_crawl_delay_slows_but_never_speeds_up(ft):
    lim = make(ft)
    lim.set_min_interval("a.test", 3)
    await lim.wait_for_host("https://a.test/")
    t0 = ft.t
    await lim.wait_for_host("https://a.test/")
    assert ft.t - t0 == pytest.approx(3.0)
    lim.set_min_interval("b.test", 0.1)  # faster than default: ignored
    await lim.wait_for_host("https://b.test/")
    await lim.wait_for_host("https://b.test/")
    assert ft.t - (t0 + 3.0) == pytest.approx(1.0)


async def test_tokens_refill_while_idle(ft):
    lim = make(ft)
    await lim.wait_for_host("https://a.test/")
    ft.t += 5  # idle: bucket is full again, but capacity is only one token
    await lim.wait_for_host("https://a.test/")
    assert ft.t == 5


@pytest.mark.parametrize("kw", [{"rate": 0}, {"rate": -1}, {"burst": 0}])
def test_invalid_config_rejected(kw):
    with pytest.raises(ValueError):
        HostRateLimiter(**kw)


async def test_url_without_host_rejected():
    with pytest.raises(ValueError):
        await HostRateLimiter().wait_for_host("not-a-url")
