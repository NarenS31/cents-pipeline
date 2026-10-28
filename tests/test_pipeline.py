import asyncio
import json
import os
import stat
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from cents_pipeline.cli import _mock_transport, app
from cents_pipeline.io import load_districts, validate_rows
from cents_pipeline.models import Status
from cents_pipeline.parsers import load_parser
from cents_pipeline.pipeline import run_audit
from cents_pipeline.queue import partition_by_host
from cents_pipeline.rate_limiter import HostRateLimiter
from cents_pipeline.scraper import PoliteFetcher

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent
PARSER = str(ROOT / "parsers" / "generic_directory.yaml")


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
            return await run_audit(ds, load_parser(PARSER), PoliteFetcher(c, HostRateLimiter(rate=1000)))
    run = asyncio.run(go())
    by = {r.district_id: r for r in run.results}
    assert by["district-a"].keyword_hits == ["budgeting", "personal finance"]  # script content ignored
    assert {c.district_id for c in run.contacts} == {"district-a", "district-b"} and len(run.contacts) == 3
    assert run.stats["blocked_by_robots"] == 1  # district-a /board is disallowed
    assert all(r.status is Status.NOT_CONTACTED for r in run.results)


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

