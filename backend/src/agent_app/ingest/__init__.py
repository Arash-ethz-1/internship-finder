"""Batch ingestion from public job-board JSON APIs, and finding boards to ingest.

No scraping and no HTML parsing beyond stripping tags out of description
fields: every board here publishes a documented public JSON endpoint, and
company discovery reads Common Crawl's documented index API rather than
crawling anything itself.
"""

from .candidates import Candidate, from_crawl, from_file, from_llm, slug_candidates
from .discovery import (
    DiscoveryReport,
    company_counts,
    load_verified,
    run_discovery,
    seed_from_toml,
)
from .profile import ProfileReport, ingest_profile, read_profile_docs
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
    "Candidate",
    "CompanyEntry",
    "CompanyResult",
    "DiscoveryReport",
    "FetchFailed",
    "IngestReport",
    "PoliteClient",
    "ProfileReport",
    "company_counts",
    "format_summary",
    "from_crawl",
    "from_file",
    "from_llm",
    "load_companies",
    "ingest_profile",
    "load_verified",
    "read_profile_docs",
    "run_discovery",
    "run_ingest",
    "seed_from_toml",
    "slug_candidates",
]
