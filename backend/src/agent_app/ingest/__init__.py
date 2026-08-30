"""Batch ingestion from public job-board JSON APIs.

No scraping and no HTML parsing beyond stripping tags out of description
fields: every board here publishes a documented public JSON endpoint.
"""

from .runner import (
    BoardNotFound,
    CompanyEntry,
    CompanyResult,
    FetchFailed,
    IngestReport,
    PoliteClient,
    format_summary,
    load_companies,
    run_ingest,
)

__all__ = [
    "BoardNotFound",
    "CompanyEntry",
    "CompanyResult",
    "FetchFailed",
    "IngestReport",
    "PoliteClient",
    "format_summary",
    "load_companies",
    "run_ingest",
]
