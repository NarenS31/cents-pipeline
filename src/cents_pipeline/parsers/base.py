"""Parser interface. Implement DistrictParser in Python, or describe a site in YAML (see yaml_parser)."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import ClassVar

from bs4 import BeautifulSoup

from ..models import DistrictRecord, FOIAContact

ROLE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("superintendent", ("superintendent",)),
    ("curriculum_cte", ("curriculum", "instruction", "cte", "career and technical", "academic")),
    ("records_officer", ("public records", "records custodian", "foia")),
    ("board_member", ("board member", "board of education", "school board", "board chair", "board vice")),
]


def classify_role(title: str) -> str:
    t = title.lower()
    for role, words in ROLE_KEYWORDS:
        if any(w in t for w in words):
            return role
    return "other"


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


class DistrictParser(ABC):
    """Knows how to find and read one family of district websites."""

    name: ClassVar[str]

    @abstractmethod
    def contact_urls(self, district: DistrictRecord) -> list[str]:
        """Absolute URLs of directory pages worth fetching for this district."""

    @abstractmethod
    def parse_contacts(self, district: DistrictRecord, url: str, html: str) -> list[FOIAContact]:
        """Extract public-official contacts from one fetched page."""

    def curriculum_urls(self, district: DistrictRecord) -> list[str]:
        """Pages to keyword-scan for K-8 financial literacy mentions. Default: none."""
        return []

    def extract_text(self, html: str) -> str:
        return html_to_text(html)
