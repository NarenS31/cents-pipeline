"""`cents` command line interface."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import httpx
import typer

from . import __version__
from .io import (
    load_coded,
    load_districts,
    render,
    rows_of,
    validate_rows,
    write_contacts_private,
    write_public,
)
from .models import CurriculumAuditResult
from .parsers import load_parser
from .pipeline import AuditRun, run_audit, uncoded_result
from .rate_limiter import HostRateLimiter
from .scraper import PoliteFetcher

app = typer.Typer(help="CENTS public-records audit pipeline.", no_args_is_help=True, add_completion=False)
Format = Annotated[str, typer.Option("--output", "-o", help="json | csv | parquet")]


def _mock_transport(site_dir: Path) -> httpx.MockTransport:
    """Serve saved pages from DIR/<host>/<path>.html for offline/CI runs. Missing files are 404."""
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path.strip("/") or "index"
        f = site_dir / req.url.host / (path if path.endswith(".txt") else path + ".html")
        return httpx.Response(200, text=f.read_text()) if f.is_file() else httpx.Response(404)
    return httpx.MockTransport(handler)


async def _crawl(districts, parser, coded, rate: float, mock: Path | None) -> AuditRun:
    transport = _mock_transport(mock) if mock else None
    async with httpx.AsyncClient(transport=transport, timeout=20) as client:
        fetcher = PoliteFetcher(client, HostRateLimiter(rate=rate))
        return await run_audit(districts, parser, fetcher, coded)


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command()
def audit(
    state: Annotated[str, typer.Option(help="Two-letter state code, e.g. NC")],
    output: Format = "json",
    districts: Annotated[Path | None, typer.Option(help="Districts JSON (default data/source/<state>_districts.json)")] = None,
    coded: Annotated[Path | None, typer.Option(help="Coded FOIA responses JSON")] = None,
    parser: Annotated[str | None, typer.Option(help="Registered parser name or YAML config path (needed with --live)")] = None,
    live: Annotated[bool, typer.Option("--live", help="Crawl district websites. Off by default; no network is used without it.")] = False,
    mock_site: Annotated[Path | None, typer.Option(help="Serve pages from a local directory instead of the network (CI)")] = None,
    rate: Annotated[float, typer.Option(help="Requests per second per host (max 1.0)")] = 1.0,
    out_dir: Annotated[Path | None, typer.Option(help="Write files here; omit to print json/csv to stdout")] = None,
    contacts_out: Annotated[Path | None, typer.Option(help="PRIVATE path for scraped officials' contacts (mode 600)")] = None,
) -> None:
    """Audit a state's districts and write a public, PII-free dataset."""
    state = state.upper()
    if rate > 1.0:
        raise typer.BadParameter("rate above 1 request/second/host is not allowed")
    dpath = districts or Path(f"data/source/{state.lower()}_districts.json")
    ds = [d for d in load_districts(dpath) if d.state == state]
    if not ds:
        typer.echo(f"no districts for {state} in {dpath}", err=True)
        raise typer.Exit(2)
    coded_map = load_coded(coded) if coded else {}
    if live or mock_site:
        if not parser:
            raise typer.BadParameter("--parser is required with --live/--mock-site")
        run = asyncio.run(_crawl(ds, load_parser(parser), coded_map, rate, mock_site))
    else:
        run = AuditRun(results=[CurriculumAuditResult.model_validate({**uncoded_result(d), **coded_map.get(d.id, {})}) for d in ds])
    validate_rows(rows_of(run.results))
    if contacts_out and run.contacts:
        write_contacts_private(run.contacts, contacts_out)
    if out_dir:
        paths = write_public(run.results, out_dir, f"{state.lower()}_audit", [output])
        typer.echo(json.dumps({"state": state, "districts": len(run.results), "files": [str(p) for p in paths], "stats": run.stats}))
    elif output == "parquet":
        raise typer.BadParameter("parquet needs --out-dir")
    else:
        typer.echo(render(rows_of(run.results), output).decode(), nl=False)


@app.command()
def validate(path: Annotated[Path, typer.Argument(help="Public dataset (json) to validate")]) -> None:
    """Check a public dataset against the schema (and that it contains no email addresses)."""
    rows = validate_rows(json.loads(path.read_text()))
    typer.echo(f"ok: {len(rows)} rows")


@app.command()
def export(
    coded: Annotated[Path, typer.Option(help="Coded results JSON (full CurriculumAuditResult rows)")],
    out_dir: Annotated[Path, typer.Option()] = Path("data"),
    stem: str = "nc_audit",
    formats: Annotated[str, typer.Option(help="Comma list of json,csv,parquet")] = "json,csv,parquet",
) -> None:
    """Validate coded results and export public datasets."""
    results = validate_rows(json.loads(coded.read_text()))
    paths = write_public(results, out_dir, stem, formats.split(","))
    typer.echo("\n".join(str(p) for p in paths))


if __name__ == "__main__":
    app()
