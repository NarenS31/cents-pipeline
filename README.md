# cents-pipeline

[![CI](https://github.com/NarenS31/cents-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/NarenS31/cents-pipeline/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange)

Polite, privacy-preserving tooling for public-records research on school districts: an async scraper that obeys
`robots.txt` and a per-host rate limit, strict Pydantic models, and PII scrubbing so only district-level results are published.
Built for the [CENTS](https://centscampaign.org) audit of K-8 financial literacy instruction in North Carolina.

## Architecture

```
                         +--------------------------------------------+
  district URLs -------> |  scraper.PoliteFetcher                     |
                         |   1. robots.txt check (cached per origin)  |
                         |   2. rate_limiter.HostRateLimiter          |
                         |        token bucket per host, 1 req/sec    |
                         |   3. httpx GET + custom User-Agent         |
                         |   4. retry on 429/503 (Retry-After)        |
                         +----------------------+---------------------+
                                                | HTML
                                                v
                                   BeautifulSoup DOM  (fetch_dom)
                                                |
                          +---------------------+----------------------+
                          v                                            v
                 contacts / keyword hits                     coded FOIA responses
                          |                                            |
                          v                                            v
                 models.FOIAContact                        models.CurriculumAuditResult
                 (scrub_pii, work email only)              (no names, emails, phones)
                 PRIVATE, mode 600                                     |
                                                                       v
                                                         public dataset (json / csv / parquet)
```

| Module | Role |
|---|---|
| `rate_limiter.py` | `HostRateLimiter`: async per-host token bucket (default 1 req/sec); `wait_for_host(url)` |
| `scraper.py` | `PoliteFetcher`, `RobotsCache`, `fetch_dom()` (httpx + BeautifulSoup) |
| `models.py` | `DistrictRecord`, `FOIAContact`, `CurriculumAuditResult`, `ScrapedDistrictRecord`, `scrub_pii()` |
| `pipeline.py`, `cli.py`, `io.py`, `parsers/` | Optional audit pipeline and `cents` CLI (needs the `cli` extra) |

## Install

From PyPI (once published; the name is not reserved yet):

```bash
pip install cents-pipeline            # library: httpx, pydantic, beautifulsoup4
pip install "cents-pipeline[cli]"     # adds the `cents` command (typer, PyYAML)
pip install "cents-pipeline[parquet]" # Parquet export
```

From source: `pip install -e ".[dev]"`.

## Quick start

```python
import asyncio, httpx
from cents_pipeline import HostRateLimiter, PoliteFetcher, fetch_dom, scrub_pii

async def main():
    async with httpx.AsyncClient(timeout=20) as client:
        fetcher = PoliteFetcher(client, HostRateLimiter(rate=1.0))   # 1 request/second/host
        dom = await fetch_dom(fetcher, "https://example.org/leadership")
        if dom:                                  # None if robots.txt disallows or the fetch failed
            print(dom.title.get_text() if dom.title else "no title")

asyncio.run(main())
print(scrub_pii("Call 919-555-0142 or pat@gmail.com"))   # -> "Call or"
```

Set `CENTS_USER_AGENT` to identify your project and a contact; the default names CENTS.

### Rate limiting
`HostRateLimiter` is keyed by the URL's host. The first request to a host is immediate, later ones are spaced
`1/rate` seconds apart, and concurrent callers for one host queue in order. Different hosts never wait on each other.
A `Crawl-delay` in robots.txt slows a host further and never speeds it up.

### Privacy rules (`models.py`)
- `scrub_pii()` removes phone numbers (a personal cell can't be told from an office line, so all go), SSN-shaped numbers,
  and addresses at consumer domains (gmail, yahoo, hotmail, outlook, icloud and similar). Tune with `ScrubRules`.
- `FOIAContact` scrubs `name` and `title` and **rejects personal-domain emails**: only official addresses are stored.
- `CurriculumAuditResult` is the only publishable model and refuses emails or phone numbers in free text.
- `ScrapedDistrictRecord.public_summary()` exposes counts only, never names or emails.

## CLI (optional)
```bash
cents audit --state NC --coded data/source/nc_coded_responses.json --output json   # offline, no network
cents audit --state NC --live --parser parsers/generic_directory.yaml --contacts-out private/contacts.json
cents export --coded data/source/nc_coded_responses.json --out-dir data
cents validate data/nc_audit.json
```
`--live` is required for any network use, and `--rate` cannot exceed 1.

## Tests
```bash
pip install -e ".[dev]"      # pytest, pytest-asyncio, ruff, typer, PyYAML, pyarrow
pytest tests/
```
Tests use a virtual clock and `httpx.MockTransport`, so they make no network requests and the rate-limit assertions are exact.

## Build
```bash
pip install flit
flit build        # dist/*.whl and dist/*.tar.gz
```

## Scope and limits
This package collects public information politely and reports what districts say in writing. It does not assess legal
compliance, and the published counts come from coded FOIA replies, not from crawling. Design notes for distributed
workers (Redis/Celery, not implemented) are in [docs/DISTRIBUTED.md](docs/DISTRIBUTED.md).

## License
MIT
