"""YAMLDistrictParser: describe a district website family in ~20 lines of YAML."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..models import DistrictRecord, FOIAContact
from .base import DistrictParser, classify_role


class YamlParserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    contact_paths: list[str] = Field(min_length=1)   # appended to the district website
    curriculum_paths: list[str] = []
    card: str                                         # CSS selector: one element per person
    person_name: str                                  # selector inside the card
    person_title: str = ""                            # selector inside the card
    confidence: float = Field(default=0.8, ge=0, le=1)


class YAMLDistrictParser(DistrictParser):
    def __init__(self, config: YamlParserConfig) -> None:
        self.config = config
        self.name = config.name

    @classmethod
    def from_file(cls, path: str | Path) -> YAMLDistrictParser:
        data: Any = yaml.safe_load(Path(path).read_text())
        try:
            return cls(YamlParserConfig.model_validate(data))
        except ValidationError as e:
            raise ValueError(f"{path}: invalid parser config\n{e}") from e

    def _urls(self, district: DistrictRecord, paths: list[str]) -> list[str]:
        return [urljoin(district.website.rstrip("/") + "/", p.lstrip("/")) for p in paths] if district.website else []

    def contact_urls(self, district: DistrictRecord) -> list[str]:
        return self._urls(district, self.config.contact_paths)

    def curriculum_urls(self, district: DistrictRecord) -> list[str]:
        return self._urls(district, self.config.curriculum_paths)

    def parse_contacts(self, district: DistrictRecord, url: str, html: str) -> list[FOIAContact]:
        c, soup, out = self.config, BeautifulSoup(html, "html.parser"), []
        for card in soup.select(c.card):
            link = card.select_one('a[href^="mailto:"]')
            name = card.select_one(c.person_name)
            if not link or not name:
                continue
            email = link["href"].removeprefix("mailto:").split("?")[0].strip()
            title = card.select_one(c.person_title).get_text(" ", strip=True) if c.person_title and card.select_one(c.person_title) else ""
            try:
                out.append(FOIAContact(district_id=district.id, role=classify_role(title), name=name.get_text(" ", strip=True),
                                       title=title, email=email, source_url=url, confidence=c.confidence))
            except ValidationError:
                continue  # malformed card: skip rather than guess
        return out
