"""Command line entry point.

Subcommands are added by the phase that makes them meaningful:

==================  =======
``init-db``         Phase 1
``ingest``          Phase 2
``embed``           Phase 4
``ingest-profile``  Phase 5
``draft-letter``    Phase 6
``chat``            Phase 9
``status``          Phase 9
``eval``            Phase 9
==================  =======
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import ConfigError, get_settings
from .db import SOURCES, table_names
from .ingest import (
    Candidate,
    PoliteClient,
    company_counts,
    format_summary,
    from_crawl,
    from_file,
    from_llm,
    load_companies,
    load_verified,
    run_discovery,
    run_ingest,
    seed_from_toml,
)
from .runtime import close_db, get_db


def cmd_init_db(_args: argparse.Namespace) -> int:
    """Create the SQLite file and every table, then report what exists."""
    settings = get_settings()
    conn = get_db()
    names = table_names(conn)
    print(f"database: {settings.db_path}")
    print(f"tables:   {', '.join(names) if names else '(none)'}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Fetch every configured board and upsert what comes back."""
    settings = get_settings()
    conn = get_db()

    # companies.toml is the seed; the companies table is the source of truth.
    # Importing on an empty table means an existing checkout keeps working
    # without a migration step.
    if seed_from_toml(conn, load_companies(settings.companies_path)):
        print(f"seeded companies from {settings.companies_path.name}")

    entries = load_verified(conn, args.source)
    if args.company:
        wanted = args.company.casefold()
        entries = [e for e in entries if wanted in e.name.casefold() or wanted == e.token]

    if not entries:
        print(
            f"No companies to ingest. Add some to {settings.companies_path}, "
            "or run: cli discover --from crawl"
        )
        return 0

    with PoliteClient(user_agent=settings.user_agent) as client:
        report = run_ingest(conn, entries, client)

    print(format_summary(report))

    total = conn.execute("SELECT count(*) FROM postings").fetchone()[0]
    print(f"\n{total} posting(s) in {settings.db_path}")

    # A board that 404s is reported, not fatal: the rest of the run still counts.
    return 1 if len(report.failures) == len(report.results) and report.results else 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Find company boards, verify them over HTTP, and record every outcome."""
    settings = get_settings()
    conn = get_db()
    seed_from_toml(conn, load_companies(settings.companies_path))

    sources: tuple[str, ...] = (args.source,) if args.source else SOURCES
    candidates: list[Candidate] = []

    if args.origin == "crawl":
        with PoliteClient(user_agent=settings.user_agent) as index_client:
            candidates = from_crawl(index_client, sources)
        print(f"{len(candidates)} candidate tokens from the Common Crawl index")
    elif args.origin == "llm":
        if not args.query:
            print("error: --from llm needs --query", file=sys.stderr)
            return 2
        candidates = from_llm(settings, args.query, limit=args.ask)
        print(f"{len(candidates)} company names from {settings.discovery_model}")
    else:
        if not args.file:
            print("error: --from file needs --file", file=sys.stderr)
            return 2
        candidates = from_file(Path(args.file))
        print(f"{len(candidates)} company names from {args.file}")

    if not candidates:
        print("nothing to verify")
        return 0

    with PoliteClient(user_agent=settings.user_agent) as client:
        report = run_discovery(conn, client, candidates, sources=sources, limit=args.limit)

    print()
    print(report.format())
    print()
    print(f"companies table: {company_counts(conn)}")
    print(f"ready to ingest: {len(load_verified(conn))}")
    return 0


def cmd_companies(args: argparse.Namespace) -> int:
    """List what is in the companies table."""
    conn = get_db()
    seed_from_toml(conn, load_companies(get_settings().companies_path))
    rows = conn.execute(
        "SELECT source, token, name, status, job_count, api_host, discovered_by "
        "FROM companies WHERE (? IS NULL OR status = ?) AND (? IS NULL OR source = ?) "
        "ORDER BY status, source, token",
        (args.status, args.status, args.source, args.source),
    ).fetchall()

    for row in rows:
        jobs = "" if row["job_count"] is None else str(row["job_count"])
        host = row["api_host"] or ""
        print(
            f"{row['status']:11} {row['source']:11} {row['token']:28} "
            f"{jobs:>6}  {row['discovered_by']:6} {host:18} {row['name'] or ''}"
        )
    print(f"\n{len(rows)} row(s). totals: {company_counts(conn)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser and its subcommands."""
    parser = argparse.ArgumentParser(
        prog="agent-app",
        description="Local agentic screener over internship postings.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show per-request logging",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_init = sub.add_parser(
        "init-db",
        help="create the SQLite database and its tables",
        description="Create data/postings.db with the full schema if it does not exist.",
    )
    p_init.set_defaults(func=cmd_init_db)

    p_ingest = sub.add_parser(
        "ingest",
        help="fetch postings from the boards in companies.toml",
        description=(
            "Fetch every configured company's job board and upsert the postings. "
            "Safe to run repeatedly: existing postings are updated in place, and "
            "postings that have disappeared from a board are kept."
        ),
    )
    p_ingest.add_argument(
        "--source",
        choices=SOURCES,
        help="only ingest boards on this source",
    )
    p_ingest.add_argument(
        "--company",
        help="only ingest companies whose display name contains this text",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    p_discover = sub.add_parser(
        "discover",
        help="find new company boards and verify them",
        description=(
            "Propose candidate companies and verify each one against the real API. "
            "Nothing is trusted because a model said it; a token counts as real "
            "only when a board returns 200. Failures are recorded too, so the "
            "same candidate is never checked twice."
        ),
    )
    p_discover.add_argument(
        "--from",
        dest="origin",
        choices=["crawl", "llm", "file"],
        default="crawl",
        help=(
            "crawl: enumerate tokens from the Common Crawl index (no API key). "
            "llm: ask Claude for company names matching --query. "
            "file: read one company name per line from --file."
        ),
    )
    p_discover.add_argument("--query", help="what to look for, with --from llm")
    p_discover.add_argument("--file", help="path to a company-name list, with --from file")
    p_discover.add_argument("--source", choices=SOURCES, help="only check this board")
    p_discover.add_argument(
        "--limit",
        type=int,
        default=200,
        help="stop after this many candidates (default 200; ~1 second each)",
    )
    p_discover.add_argument(
        "--ask",
        type=int,
        default=50,
        help="how many company names to request, with --from llm (default 50)",
    )
    p_discover.set_defaults(func=cmd_discover)

    p_companies = sub.add_parser(
        "companies",
        help="list the companies table",
        description="Show every discovered company and its verification status.",
    )
    p_companies.add_argument("--status", choices=["verified", "dead", "unresolved"])
    p_companies.add_argument("--source", choices=SOURCES)
    p_companies.set_defaults(func=cmd_companies)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        close_db()


if __name__ == "__main__":
    raise SystemExit(main())
