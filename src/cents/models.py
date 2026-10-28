"""Strict data models. Public outputs use only CurriculumAuditResult; contacts stay private."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.lower()
        if not EMAIL_RE.match(v):
            raise ValueError("invalid email")
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
        if v and ("@" in v or re.search(r"\d{3}[-.\s]\d{3}[-.\s]\d{4}", v)):
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


PUBLIC_COLUMNS = list(CurriculumAuditResult.model_fields)
