"""Strict data models. Public outputs use only CurriculumAuditResult; contacts stay private."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ANY_EMAIL_RE = re.compile(r"[^@\s]+@([^@\s]+\.[^@\s.,;)]+)")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
PERSONAL_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com",
    "icloud.com", "me.com", "aol.com", "msn.com", "proton.me", "protonmail.com",
})


class ScrubRules(BaseModel):
    """What to strip from scraped free text before it is stored. Public officials' *work* email stays."""

    model_config = ConfigDict(frozen=True)

    strip_phones: bool = True            # cannot tell a cell from an office line, so all numbers go
    strip_ssn: bool = True
    strip_personal_emails: bool = True   # addresses at consumer domains (gmail, yahoo, ...)
    personal_email_domains: frozenset[str] = PERSONAL_EMAIL_DOMAINS
    placeholder: str = ""


DEFAULT_RULES = ScrubRules()


def scrub_pii(text: str, rules: ScrubRules = DEFAULT_RULES) -> str:
    """Remove phone numbers, SSN-shaped numbers and personal-domain emails from free text."""
    if rules.strip_ssn:
        text = SSN_RE.sub(rules.placeholder, text)  # before phones: an SSN is not a phone number
    if rules.strip_phones:
        text = PHONE_RE.sub(rules.placeholder, text)
    if rules.strip_personal_emails:
        text = _ANY_EMAIL_RE.sub(
            lambda m: rules.placeholder if m.group(1).lower() in rules.personal_email_domains else m.group(0), text)
    return re.sub(r"\s{2,}", " ", text).strip()


class Status(StrEnum):
    """Public status of a district, matching the CENTS tracker's display statuses."""

    NOT_CONTACTED = "not_contacted"
    AWAITING_RESPONSE = "awaiting_response"
    RESPONSE_IN_PROGRESS = "response_in_progress"
    NO_RESPONSE_AFTER_FOLLOWUP = "no_response_after_followup"
    UNCLEAR_RESPONSE = "unclear_response"
    CONFIRMED_IMPLEMENTING = "confirmed_implementing"
    CONFIRMED_NOT_IMPLEMENTING = "confirmed_not_implementing"


class ResponseType(StrEnum):
    PROVIDED_CURRICULUM = "provided_curriculum"
    NO_DEDICATED_COURSE = "no_dedicated_course"
    PARTIAL_OR_REFERRED = "partial_or_referred"
    DENIED_OR_DEFERRED = "denied_or_deferred"
    ACKNOWLEDGMENT_ONLY = "acknowledgment_only"
    AMBIGUOUS = "ambiguous"
    NO_RESPONSE = "no_response"


class OutreachStatus(StrEnum):
    NOT_CONTACTED = "not_contacted"
    AWAITING_RESPONSE = "awaiting_response"
    RESPONDED = "responded"
    NO_RESPONSE_AFTER_FOLLOWUP = "no_response_after_followup"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DistrictRecord(_Strict):
    """A school district to audit. `id` is a stable slug; `leaid` is the NCES id when known."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=2)
    state: str = Field(pattern=r"^[A-Z]{2}$")
    leaid: str | None = Field(default=None, pattern=r"^\d{7}$")
    enrollment: int | None = Field(default=None, ge=0)
    website: str | None = Field(default=None, pattern=r"^https?://[^\s/]+")


class FOIAContact(_Strict):
    """A public official's contact record. PRIVATE: never written to public datasets."""

    district_id: str
    role: Literal["superintendent", "curriculum_cte", "board_member", "records_officer", "other"]
    name: str = Field(min_length=1)
    title: str = ""
    email: str
    source_url: str = Field(pattern=r"^https?://")
    confidence: float = Field(ge=0, le=1)
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name", "title")
    @classmethod
    def _scrub(cls, v: str) -> str:
        return scrub_pii(v)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.lower()
        if not EMAIL_RE.match(v):
            raise ValueError("invalid email")
        if v.rsplit("@", 1)[1] in PERSONAL_EMAIL_DOMAINS:
            raise ValueError("personal email domains are not collected; only official addresses")
        return v


class CurriculumAuditResult(_Strict):
    """District-level audit outcome. Safe to publish: contains no personal data."""

    district_id: str
    district_name: str
    state: str = Field(pattern=r"^[A-Z]{2}$")
    outreach_status: OutreachStatus
    status: Status
    response_type: ResponseType
    request_sent: date | None = None
    follow_up_sent: date | None = None
    response_date: date | None = None
    needs_review: bool = False
    keyword_hits: list[str] = Field(default_factory=list)
    pages_checked: int = Field(default=0, ge=0)
    public_note: str | None = None

    @field_validator("public_note")
    @classmethod
    def _no_contact_info(cls, v: str | None) -> str | None:
        if v and ("@" in v or PHONE_RE.search(v)):
            raise ValueError("public_note must not contain emails or phone numbers")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> CurriculumAuditResult:
        if self.response_type is ResponseType.NO_RESPONSE and self.response_date:
            raise ValueError("no_response cannot have a response_date")
        if self.status is Status.NOT_CONTACTED and self.request_sent:
            raise ValueError("not_contacted cannot have a request_sent date")
        if self.status is Status.CONFIRMED_NOT_IMPLEMENTING and (
            self.response_type is not ResponseType.NO_DEDICATED_COURSE
        ):
            raise ValueError("confirmed_not_implementing requires response_type=no_dedicated_course")
        return self


class ScrapedDistrictRecord(_Strict):
    """Everything crawled for one district. Contains personal data: keep private; publish `public_summary()`."""

    district: DistrictRecord
    contacts: list[FOIAContact] = Field(default_factory=list)
    keyword_hits: list[str] = Field(default_factory=list)
    pages_checked: int = Field(default=0, ge=0)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _contacts_belong_here(self) -> ScrapedDistrictRecord:
        if any(c.district_id != self.district.id for c in self.contacts):
            raise ValueError("contact belongs to a different district")
        return self

    def public_summary(self) -> dict:
        """District-level facts only: no names, titles or emails."""
        return {"district_id": self.district.id, "district_name": self.district.name, "state": self.district.state,
                "contacts_found": len(self.contacts), "keyword_hits": self.keyword_hits, "pages_checked": self.pages_checked}


PUBLIC_COLUMNS = list(CurriculumAuditResult.model_fields)
