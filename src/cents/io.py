"""Load/validate/export datasets. Public exports contain CurriculumAuditResult columns only."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .models import PUBLIC_COLUMNS, CurriculumAuditResult, DistrictRecord, FOIAContact


def load_districts(path: str | Path) -> list[DistrictRecord]:
    return [DistrictRecord.model_validate(r) for r in json.loads(Path(path).read_text())]


def load_coded(path: str | Path) -> dict[str, dict]:
    rows = json.loads(Path(path).read_text())
    return {r["district_id"]: r for r in rows}


def rows_of(results: list[CurriculumAuditResult]) -> list[dict]:
    return [r.model_dump(mode="json") for r in results]


def validate_rows(rows: list[dict]) -> list[CurriculumAuditResult]:
    out = [CurriculumAuditResult.model_validate(r) for r in rows]
    ids = [r.district_id for r in out]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate district_id in dataset")
    for r in rows:
        if "@" in json.dumps(r):
            raise ValueError(f"possible email address in public row {r.get('district_id')}")
    return out


def render(rows: list[dict], fmt: str) -> bytes:
    if fmt == "json":
        return (json.dumps(rows, indent=2, ensure_ascii=False) + "\n").encode()
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=PUBLIC_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "keyword_hits": ";".join(r["keyword_hits"])})
        return buf.getvalue().encode()
    if fmt == "parquet":
        import pyarrow as pa  # optional dependency
        import pyarrow.parquet as pq

        sink = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(rows), sink)
        return sink.getvalue()
    raise ValueError(f"unknown format {fmt!r}")


def write_public(results: list[CurriculumAuditResult], out_dir: str | Path, stem: str, formats: list[str]) -> list[Path]:
    rows = rows_of(results)
    validate_rows(rows)
    out = []
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        p = Path(out_dir) / f"{stem}.{fmt}"
        p.write_bytes(render(rows, fmt))
        out.append(p)
    return out


def write_contacts_private(contacts: list[FOIAContact], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([c.model_dump(mode="json") for c in contacts], indent=2))
    p.chmod(0o600)
