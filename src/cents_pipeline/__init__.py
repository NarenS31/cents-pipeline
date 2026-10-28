"""Polite public-records discovery and K-8 financial literacy audit pipeline."""

__version__ = "0.1.0"

from .models import CurriculumAuditResult, DistrictRecord, FOIAContact, ScrapedDistrictRecord, scrub_pii
from .rate_limiter import HostRateLimiter
from .scraper import PoliteFetcher, RobotsCache, fetch_dom

__all__ = [
           "CurriculumAuditResult",
           "DistrictRecord",
           "FOIAContact",
           "HostRateLimiter",
           "PoliteFetcher",
           "RobotsCache",
           "ScrapedDistrictRecord",
           "fetch_dom",
           "scrub_pii",
]
