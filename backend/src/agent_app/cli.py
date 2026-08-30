"""Command line entry point.

Subcommands are added by the phase that makes them meaningful:

===============  =====
``init-db``      Phase 1
``ingest``       Phase 2
``embed``        Phase 4
``ingest-profile`` Phase 5
``draft-letter`` Phase 6
``chat``         Phase 9
``status``       Phase 9
``eval``         Phase 9
===============  =====
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .config import ConfigError, get_settings
from .db import table_names
from .runtime import close_db, get_db


def cmd_init_db(_args: argparse.Namespace) -> int:
    """Create the SQLite file and every table, then report what exists."""
    settings = get_settings()
    conn = get_db()
    names = table_names(conn)
    print(f"database: {settings.db_path}")
    print(f"tables:   {', '.join(names) if names else '(none)'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser and its subcommands."""
    parser = argparse.ArgumentParser(
        prog="agent-app",
        description="Local agentic screener over internship postings.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_init = sub.add_parser(
        "init-db",
        help="create the SQLite database and its tables",
        description="Create data/postings.db with the full schema if it does not exist.",
    )
    p_init.set_defaults(func=cmd_init_db)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        close_db()


if __name__ == "__main__":
    raise SystemExit(main())
