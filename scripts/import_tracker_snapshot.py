"""Build data/source/nc_*.json from the CENTS tracker's records.json (public fields only).

Usage: python scripts/import_tracker_snapshot.py ~/cents-v2/lib/tracker/records.json
Drops respondent names and reply text. Fails if any public field would contain contact info.
"""
import json
import sys
from pathlib import Path

from cents.models import CurriculumAuditResult


def status_of(r: dict) -> str:
    """Mirror displayStatusOf() in cents-v2 lib/tracker/types.ts."""
    if r["outreachStatus"] == "no_response_after_followup":
        return "no_response_after_followup"
    if r.get("responseInProgress") and r["outreachStatus"] == "awaiting_response":
        return "response_in_progress"
    c = r["classification"]
    if c == "not_yet_contacted":
        return "not_contacted" if r["outreachStatus"] == "not_contacted" else "awaiting_response"
    return "unclear_response" if c == "unclear_or_unresponsive" else c


def main(src: str, out: str = "data/source") -> None:
    recs = json.loads(Path(src).read_text())
    districts, coded = [], []
    for r in recs:
        districts.append({"id": r["slug"], "name": r["name"], "state": "NC"})
        row = {
            "district_id": r["slug"], "district_name": r["name"], "state": "NC",
            "outreach_status": r["outreachStatus"], "status": status_of(r),
            "response_type": r["responseType"], "request_sent": r.get("requestSentDate"),
            "follow_up_sent": r.get("followUpSentDate"), "response_date": r.get("responseDate"),
            "needs_review": bool(r.get("needsReview")), "public_note": r.get("publicNote"),
        }
        CurriculumAuditResult.model_validate(row)  # fail loudly on bad rows
        coded.append(row)
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / "nc_districts.json").write_text(json.dumps(districts, indent=2) + "\n")
    (Path(out) / "nc_coded_responses.json").write_text(json.dumps(coded, indent=2) + "\n")
    print(f"{len(coded)} districts written to {out}")


if __name__ == "__main__":
    main(*sys.argv[1:])
