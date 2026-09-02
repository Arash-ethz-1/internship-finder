"""Batch ingestion from public job-board JSON APIs, and finding boards to ingest.

No scraping and no HTML parsing beyond stripping tags out of description
fields: every board here publishes a documented public JSON endpoint, and
company discovery reads Common Crawl's documented index API rather than
crawling anything itself.
"""

from .candidates import Candidate, from_crawl, from_file, from_llm, slug_candidates
from .chunks import (
    PostingChunkReport,
    chunk_pending_postings,
    pending_posting_ids,
)
from .discovery import (
    DiscoveryReport,
    company_counts,
    load_verified,
    run_discovery,
    seed_from_toml,
)
from .locations import (
    LocationReport,
    index_pending_locations,
    index_posting,
    reindex_all_locations,
    top_unresolved,
)
from .locations import coverage as location_coverage
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
    reconcile_closed,
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
    "LocationReport",
    "PoliteClient",
    "PostingChunkReport",
    "ProfileReport",
    "chunk_pending_postings",
    "company_counts",
    "format_summary",
    "from_crawl",
    "from_file",
    "from_llm",
    "index_pending_locations",
    "index_posting",
    "ingest_profile",
    "load_companies",
    "load_verified",
    "location_coverage",
    "pending_posting_ids",
    "read_profile_docs",
    "reconcile_closed",
    "reindex_all_locations",
    "run_discovery",
    "run_ingest",
    "seed_from_toml",
    "slug_candidates",
    "top_unresolved",
]
