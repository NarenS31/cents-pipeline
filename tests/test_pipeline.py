import asyncio
import json
import os
import stat
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from cents.cli import _mock_transport, app
from cents.crawl.fetcher import PoliteFetcher
from cents.crawl.ratelimit import AsyncHostRateLimiter
from cents.crawl.robots import RobotsCache
from cents.io import load_districts, validate_rows
from cents.models import CurriculumAuditResult, FOIAContact, Status
from cents.parsers import load_parser
from cents.pipeline import run_audit
from cents.queue import partition_by_host

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent
PARSER = str(ROOT / "parsers" / "generic_directory.yaml")


class FakeTime:
    def __init__(self): self.t = 0.0; self.sleeps = []
    def now(self): return self.t
    async def sleep(self, s): self.sleeps.append(s); self.t += s


def test_limiter_spaces_same_host_one_second():
    ft = FakeTime(); lim = AsyncHostRateLimiter(clock=ft.now, sleep=ft.sleep)
    stamps = []
    async def go():
        for _ in range(4):
            await lim.acquire("a.test"); stamps.append(ft.t)
    asyncio.run(go())
    assert [round(b - a, 6) for a, b in zip(stamps, stamps[1:])] == [1.0, 1.0, 1.0]


def test_limiter_hosts_independent_and_concurrent_waiters_serialised():
    ft = FakeTime(); lim = AsyncHostRateLimiter(clock=ft.now, sleep=ft.sleep)
    async def go():
        await lim.acquire("a.test"); await lim.acquire("b.test"); await lim.acquire("c.test")
    asyncio.run(go()); assert ft.t == 0  # first hit per host is immediate
    times = []
    async def one():
        await lim.acquire("a.test"); times.append(ft.t)
    async def many(): await asyncio.gather(*(one() for _ in range(3)))
    asyncio.run(many())
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(sorted(times), sorted(times)[1:]))


def test_crawl_delay_slows_host():
    ft = FakeTime(); lim = AsyncHostRateLimiter(clock=ft.now, sleep=ft.sleep)
    lim.set_min_interval("a.test", 3)
    async def go():
        await lim.acquire("a.test"); t0 = ft.t; await lim.acquire("a.test"); return ft.t - t0
    assert asyncio.run(go()) == pytest.approx(3.0)


def test_robots_policy():
    r = RobotsCache("CENTS-Audit")
    r.load("https://a.test", 200, "User-agent: *\nDisallow: /private")
    r.load("https://b.test", 404); r.load("https://c.test", 500); r.load("https://d.test", None)
    assert r.allowed("https://a.test/x") and not r.allowed("https://a.test/private/x")
    assert r.allowed("https://b.test/x")
    assert not r.allowed("https://c.test/x") and not r.allowed("https://d.test/x")


def _fetcher(client, lim=None):
    return PoliteFetcher(client, lim or AsyncHostRateLimiter(rate=1000), max_retries=1)


def test_fetcher_user_agent_robots_and_retry_after():
    seen = []
    calls = {"n": 0}
    def handler(req):
        seen.append((req.url.path, req.headers["user-agent"]))
        if req.url.path == "/robots.txt": return httpx.Response(200, text="User-agent: *\nDisallow: /secret")
        if req.url.path == "/flaky":
            calls["n"] += 1
            return httpx.Response(429 if calls["n"] == 1 else 200, headers={"Retry-After": "0"}, text="ok")
        return httpx.Response(200, text="hi")
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            f = _fetcher(c)
            return f, await f.get("https://x.test/secret"), await f.get("https://x.test/flaky"), await f.get("https://x.test/page")
    f, blocked, flaky, page = asyncio.run(go())
    assert blocked is None and flaky.text == "ok" and page.text == "hi"
    assert f.stats["blocked_by_robots"] == 1 and f.stats["throttled"] == 1
    assert all(ua.startswith("CENTS-Audit/") for _, ua in seen)
    assert [p for p, _ in seen].count("/robots.txt") == 1  # fetched once per origin


def test_fetcher_never_exceeds_one_request_per_second_per_host():
    ft = FakeTime(); lim = AsyncHostRateLimiter(clock=ft.now, sleep=ft.sleep); stamps = []
    def handler(req): stamps.append(ft.t); return httpx.Response(404 if "robots" in req.url.path else 200, text="x")
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            f = _fetcher(c, lim)
            await asyncio.gather(*(f.get(f"https://h.test/p{i}") for i in range(4)))
    asyncio.run(go())
    assert len(stamps) == 5 and all(b - a >= 1.0 - 1e-9 for a, b in zip(stamps, stamps[1:]))


def test_yaml_parser_and_registry():
    p = load_parser(PARSER); d = load_districts(FIX / "districts.json")[0]
    html = (FIX / "site/district-a.test/about/leadership.html").read_text()
    cs = p.parse_contacts(d, "https://district-a.test/about/leadership", html)
    assert [(c.role, c.email) for c in cs] == [("superintendent", "pat@district-a.test"), ("curriculum_cte", "sam@district-a.test")]
    with pytest.raises(ValueError): load_parser("nope")


def test_bad_yaml_config_rejected(tmp_path):
    f = tmp_path / "p.yaml"; f.write_text("name: x\ncontact_paths: []\ncard: li\nperson_name: b\nextra: 1\n")
    with pytest.raises(ValueError): load_parser(str(f))


def test_pipeline_end_to_end_with_robots_and_keywords():
    ds = load_districts(FIX / "districts.json")
    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(FIX / "site")) as c:
            return await run_audit(ds, load_parser(PARSER), PoliteFetcher(c, AsyncHostRateLimiter(rate=1000)))
    run = asyncio.run(go())
    by = {r.district_id: r for r in run.results}
    assert by["district-a"].keyword_hits == ["budgeting", "personal finance"]  # script content ignored
    assert {c.district_id for c in run.contacts} == {"district-a", "district-b"} and len(run.contacts) == 3
    assert run.stats["blocked_by_robots"] == 1  # district-a /board is disallowed
    assert all(r.status is Status.NOT_CONTACTED for r in run.results)


def test_models_reject_bad_data():
    base = dict(district_id="a", district_name="A Schools", state="NC", outreach_status="responded", status="unclear_response", response_type="ambiguous")
    CurriculumAuditResult(**base)
    for bad in [dict(public_note="call me a@b.org"), dict(public_note="555-123-4567"), dict(state="North Carolina"),
                dict(status="confirmed_not_implementing"), dict(response_type="no_response", response_date="2026-08-01"), dict(extra=1)]:
        with pytest.raises(ValueError): CurriculumAuditResult(**{**base, **bad})
    with pytest.raises(ValueError): FOIAContact(district_id="a", role="other", name="x", email="nope", source_url="https://a.test", confidence=0.5)


def test_published_snapshot_matches_verified_counts_and_has_no_pii():
    rows = json.loads((ROOT / "data/source/nc_coded_responses.json").read_text())
    results = validate_rows(rows)
    from collections import Counter
    assert Counter(r.status.value for r in results) == {
        "confirmed_not_implementing": 23, "confirmed_implementing": 1, "unclear_response": 22,
        "response_in_progress": 3, "awaiting_response": 36, "no_response_after_followup": 30}
    assert len(results) == 115


def test_partition_keeps_shared_hosts_together():
    ds = load_districts(FIX / "districts.json")
    ds[1] = ds[1].model_copy(update={"website": ds[0].website})
    batches = partition_by_host(ds, 2)
    assert len(batches) == 1 and len(batches[0]) == 2


def test_cli_audit_mock_site_writes_public_and_private_separately(tmp_path):
    out, priv = tmp_path / "out", tmp_path / "private" / "c.json"
    r = CliRunner().invoke(app, ["audit", "--state", "NC", "--districts", str(FIX / "districts.json"), "--parser", PARSER,
                                 "--mock-site", str(FIX / "site"), "--rate", "1", "--out-dir", str(out), "--contacts-out", str(priv), "-o", "csv"])
    assert r.exit_code == 0, r.output
    text = (out / "nc_audit.csv").read_text()
    assert "@" not in text and "Pat Example" not in text and "district-a" in text
    assert stat.S_IMODE(os.stat(priv).st_mode) == 0o600 and "pat@district-a.test" in priv.read_text()


def test_cli_json_to_stdout_and_rate_cap():
    r = CliRunner().invoke(app, ["audit", "--state", "NC", "--districts", str(FIX / "districts.json"), "-o", "json"])
    assert r.exit_code == 0 and len(json.loads(r.output)) == 2
    assert CliRunner().invoke(app, ["audit", "--state", "NC", "--districts", str(FIX / "districts.json"), "--rate", "5"]).exit_code != 0


def test_parquet_roundtrip(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    r = CliRunner().invoke(app, ["export", "--coded", str(ROOT / "data/source/nc_coded_responses.json"), "--out-dir", str(tmp_path), "--formats", "parquet"])
    assert r.exit_code == 0, r.output
    assert pq.read_table(tmp_path / "nc_audit.parquet").num_rows == 115
