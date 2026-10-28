"""Audit pipeline: crawl each district politely, then merge with coded FOIA responses."""
from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .crawl.fetcher import PoliteFetcher
from .keywords import keyword_hits
from .models import CurriculumAuditResult, DistrictRecord, FOIAContact, OutreachStatus, ResponseType, Status
from .parsers.base import DistrictParser


@dataclass
class AuditRun:
    results: list[CurriculumAuditResult] = field(default_factory=list)
    contacts: list[FOIAContact] = field(default_factory=list)  # private; never exported publicly
    stats: dict[str, int] = field(default_factory=dict)


def uncoded_result(d: DistrictRecord) -> dict:
    """Baseline for a district with no coded FOIA record yet."""
    return dict(district_id=d.id, district_name=d.name, state=d.state, outreach_status=OutreachStatus.NOT_CONTACTED,
                status=Status.NOT_CONTACTED, response_type=ResponseType.NO_RESPONSE)


async def _crawl_district(d: DistrictRecord, parser: DistrictParser, fetcher: PoliteFetcher):
    contacts: list[FOIAContact] = []
    hits: set[str] = set()
    pages = 0
    for url in parser.contact_urls(d):  # sequential within a district: one host
        r = await fetcher.get(url)
        if r and r.status < 400:
            pages += 1
            contacts += parser.parse_contacts(d, r.url, r.text)
    for url in parser.curriculum_urls(d):
        r = await fetcher.get(url)
        if r and r.status < 400:
            pages += 1
            hits.update(keyword_hits(parser.extract_text(r.text)))
    return contacts, sorted(hits), pages


async def run_audit(districts: Iterable[DistrictRecord], parser: DistrictParser, fetcher: PoliteFetcher,
                    coded: Mapping[str, dict] | None = None) -> AuditRun:
    """Districts run concurrently (different hosts); the limiter serialises requests per host."""
    districts = list(districts)
    coded = coded or {}
    crawled = await asyncio.gather(*(_crawl_district(d, parser, fetcher) for d in districts))
    run = AuditRun()
    for d, (contacts, hits, pages) in zip(districts, crawled, strict=True):
        record = {**uncoded_result(d), **coded.get(d.id, {}), "keyword_hits": hits, "pages_checked": pages}
        run.results.append(CurriculumAuditResult.model_validate(record))
        run.contacts += contacts
    run.stats = {**fetcher.stats, "districts": len(districts), "contacts": len(run.contacts)}
    return run
