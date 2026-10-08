"""FileSource: postings from a CSV or XLSX file instead of a website — the
synthetic demo data (`make demo`) and, potentially, historical backfill.

The file goes through exactly the same run loop as a scraped page (raw copy,
masking, age window, upsert), so the demo exercises the real pipeline.

Columns: by default a CSV column is read into the JobPosting field of the
same name. A `columns` mapping in the source's config renames them:
    columns: {title: judul, company_name: perusahaan, ...}
`source_job_id` and `title` are required; `url` defaults to file://<path>#<id>.
Optional `posted_days_ago` / `valid_days_ahead` columns are turned into dates
relative to today, and `extra_<name>` columns land in JobPosting.extra.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import fields
from datetime import timedelta
from pathlib import Path

from core.base import JobPosting, JobSource
from core.errors import LayoutChanged
from core.timeutil import today_jakarta

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTING_FIELDS = [f.name for f in fields(JobPosting) if f.name not in ("source", "extra")]
INT_FIELDS = {"salary_min", "salary_max"}


class FileSource(JobSource):
    source = "sample"

    def __init__(self, *args, path: str | Path | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        configured = path or self.settings.get("path")
        if not configured:
            raise ValueError(f"{self.source}: no file path configured")
        self.path = Path(configured)
        if not self.path.is_absolute():
            self.path = REPO_ROOT / self.path
        self.columns: dict[str, str] = self.settings.get("columns") or {}
    def list_jobs(self) -> Iterator[str]:
        yield str(self.path)

    def fetch(self, url: str) -> str:
        path = Path(url)
        if path.suffix.lower() == ".xlsx":
            return _xlsx_to_csv(path)
        return path.read_text(encoding="utf-8-sig")

    def parse(self, raw: str, url: str) -> list[JobPosting]:
        reader = csv.DictReader(io.StringIO(raw))
        postings = []
        for line_no, row in enumerate(reader, start=2):
            values = {f: (row.get(self.columns.get(f, f)) or "").strip() or None for f in POSTING_FIELDS}
            if not values["source_job_id"] or not values["title"]:
                raise LayoutChanged(f"{url}:{line_no}: source_job_id and title are required")
            for f in INT_FIELDS:
                values[f] = int(float(values[f])) if values[f] else None
            # Relative dates keep a static file inside the age window forever.
            if row.get("posted_days_ago"):
                values["posted_at"] = (today_jakarta() - timedelta(days=int(row["posted_days_ago"]))).isoformat()
            if row.get("valid_days_ahead"):
                values["valid_through"] = (today_jakarta() + timedelta(days=int(row["valid_days_ahead"]))).isoformat()
            values["url"] = values["url"] or f"file://{Path(url).name}#{values['source_job_id']}"
            extra = {k: v for k, v in row.items() if k and k.startswith("extra_") and v}
            postings.append(JobPosting(source=self.source, extra={k[6:]: v for k, v in extra.items()}, **values))
        return postings


def _xlsx_to_csv(path: Path) -> str:
    from openpyxl import load_workbook

    sheet = load_workbook(path, read_only=True, data_only=True).active
    out = io.StringIO()
    writer = csv.writer(out)
    for row in sheet.iter_rows(values_only=True):
        writer.writerow(["" if v is None else v for v in row])
    return out.getvalue()
