"""Keyword audit of curriculum pages for K-8 financial literacy mentions."""
from __future__ import annotations

import re

KEYWORDS = (
    "financial literacy", "personal finance", "personal financial", "money management",
    "economics", "budgeting", "saving and investing", "consumer education",
)
_PATTERNS = {k: re.compile(r"\b" + re.escape(k) + r"\b", re.IGNORECASE) for k in KEYWORDS}


def keyword_hits(text: str) -> list[str]:
    return sorted(k for k, p in _PATTERNS.items() if p.search(text))
