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

from .config import ConfigError, get_settings
from .db import SOURCES, table_names
from .ingest import PoliteClient, format_summary, load_companies, run_ingest
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

    entries = load_companies(settings.companies_path)
    if args.source:
        entries = [e for e in entries if e.source == args.source]
    if args.company:
        wanted = args.company.casefold()
        entries = [e for e in entries if wanted in e.name.casefold() or wanted == e.token]

    if not entries:
        print(f"No companies to ingest. Add some to {settings.companies_path}")
        return 0

    with PoliteClient(user_agent=settings.user_agent) as client:
        report = run_ingest(conn, entries, client)

    print(format_summary(report))

    total = conn.execute("SELECT count(*) FROM postings").fetchone()[0]
    print(f"\n{total} posting(s) in {settings.db_path}")

    # A board that 404s is reported, not fatal: the rest of the run still counts.
    return 1 if len(report.failures) == len(report.results) and report.results else 0


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
