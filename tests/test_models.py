import pytest
from pydantic import ValidationError

from cents_pipeline.models import (
    CurriculumAuditResult,
    DistrictRecord,
    FOIAContact,
    ScrapedDistrictRecord,
    ScrubRules,
    scrub_pii,
)

BASE = {"district_id": "a", "district_name": "A Schools", "state": "NC", "outreach_status": "responded",
        "status": "unclear_response", "response_type": "ambiguous"}


@pytest.mark.parametrize("raw,clean", [
    ("Call me on my cell 919-555-0142 today", "Call me on my cell today"),
    ("Office (919) 555-0142 or +1 919.555.0142", "Office or"),
    ("SSN 123-45-6789 on file", "SSN on file"),
    ("Reach pat.smith@gmail.com or pat@district.k12.nc.us", "Reach or pat@district.k12.nc.us"),
    ("Grades K-8, room 2024, 115C-81.65", "Grades K-8, room 2024, 115C-81.65"),  # not phone-shaped
])
def test_scrub_pii(raw, clean):
    assert scrub_pii(raw) == clean


def test_scrub_rules_can_be_relaxed():
    assert "919-555-0142" in scrub_pii("919-555-0142", ScrubRules(strip_phones=False))
    assert scrub_pii("919-555-0142", ScrubRules(placeholder="[removed]")) == "[removed]"


def test_contact_scrubs_name_and_title_and_lowercases_email():
    c = FOIAContact(district_id="a", role="other", name="Pat 919-555-0142", title="Director (cell 919 555 0142)",
                    email="Pat@District.K12.NC.US", source_url="https://d.test/s", confidence=0.9)
    assert c.name == "Pat" and "555" not in c.title and c.email == "pat@district.k12.nc.us"


@pytest.mark.parametrize("email", ["pat@gmail.com", "pat@Yahoo.com", "nope", "a@b"])
def test_contact_rejects_personal_or_invalid_email(email):
    with pytest.raises(ValidationError):
        FOIAContact(district_id="a", role="other", name="x", email=email, source_url="https://d.test", confidence=0.5)


def test_contact_confidence_bounds_and_extra_forbidden():
    kw = {"district_id": "a", "role": "other", "name": "x", "email": "x@d.org", "source_url": "https://d.test"}
    with pytest.raises(ValidationError):
        FOIAContact(**kw, confidence=1.5)
    with pytest.raises(ValidationError):
        FOIAContact(**kw, confidence=0.5, home_phone="1")


def test_district_record_validation():
    DistrictRecord(id="a-b", name="AB", state="NC", leaid="3700001", website="https://x.test")
    for bad in ({"id": "A B"}, {"state": "North Carolina"}, {"leaid": "123"}, {"website": "ftp://x"}, {"enrollment": -1}):
        with pytest.raises(ValidationError):
            DistrictRecord(**{"id": "a", "name": "AB", "state": "NC", **bad})


@pytest.mark.parametrize("bad", [
    {"public_note": "call me a@b.org"}, {"public_note": "555-123-4567"}, {"public_note": "(919) 555 1234"},
    {"state": "North Carolina"}, {"status": "confirmed_not_implementing"},
    {"response_type": "no_response", "response_date": "2026-08-01"},
    {"status": "not_contacted", "request_sent": "2026-07-28"}, {"extra": 1},
])
def test_audit_result_rejects_bad_data(bad):
    CurriculumAuditResult(**BASE)
    with pytest.raises(ValidationError):
        CurriculumAuditResult(**{**BASE, **bad})


def test_scraped_record_public_summary_has_no_personal_data():
    d = DistrictRecord(id="a", name="A Schools", state="NC")
    c = FOIAContact(district_id="a", role="superintendent", name="Pat Example", email="pat@a.k12.nc.us",
                    source_url="https://a.test/s", confidence=0.9)
    s = ScrapedDistrictRecord(district=d, contacts=[c], keyword_hits=["budgeting"], pages_checked=2).public_summary()
    assert s["contacts_found"] == 1 and "Pat" not in str(s) and "@" not in str(s)


def test_scraped_record_rejects_foreign_contact():
    d = DistrictRecord(id="a", name="A Schools", state="NC")
    c = FOIAContact(district_id="other", role="other", name="x", email="x@a.org", source_url="https://a.test", confidence=0.5)
    with pytest.raises(ValidationError):
        ScrapedDistrictRecord(district=d, contacts=[c])
