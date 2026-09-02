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
``sync-email``      Phase 10
==================  =======
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import ConfigError, Settings, get_settings
from .db import SOURCES, stats, table_names
from .ingest import (
    Candidate,
    PoliteClient,
    chunk_pending_postings,
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
from .runtime import close_db, get_db, reset_bm25_index, reset_vectors


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

    # Chunking is local and free, so it runs as part of every ingest: a
    # posting is searchable by keyword the moment it lands, without waiting
    # for the one command that costs money. `embed` only adds the vectors.
    chunked = chunk_pending_postings(conn)
    if chunked.pending:
        print("")
        print(chunked.format())

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


def cmd_draft_letter(args: argparse.Namespace) -> int:
    """Draft a motivational letter for one posting."""
    from .core.letters import LetterError, draft_letter

    try:
        letter = draft_letter(args.posting_id, k=args.chunks)
    except (ConfigError, KeyError, LetterError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"wrote {letter.path}")
    if letter.todos:
        print(f"\n{len(letter.todos)} marker(s) left for you to fill in:")
        for todo in letter.todos:
            print(f"  {todo}")
    print("\ngrounded in:")
    for hit in letter.grounding:
        print(f"  {hit.score:.4f}  {hit.profile_doc}#{hit.ordinal}")
    return 0


def cmd_ingest_profile(_args: argparse.Namespace) -> int:
    """Chunk and embed the author's project write-ups."""
    from .core.embeddings import EmbeddingError, embed_all_pending
    from .ingest.profile import ingest_profile

    settings = get_settings()
    settings.ensure_dirs()
    conn = get_db()

    report = ingest_profile(conn, settings)
    print(report.format())
    if report.documents == 0:
        print("")
        print(f"Add one markdown file per project to {settings.profile_dir}")
        print("See profile/README.md for the format.")
        return 0

    try:
        embedded = embed_all_pending(conn, settings=settings)
    except (ConfigError, EmbeddingError) as exc:
        print("", file=sys.stderr)
        print(f"chunked, but not embedded: {exc}", file=sys.stderr)
        return 2

    reset_vectors()
    print(embedded.format())
    return 0


def cmd_embed(args: argparse.Namespace) -> int:
    """Embed every chunk that does not have a vector yet.

    ``--export`` and ``--import`` split that in half so the embedding can run
    on a machine with more compute than a laptop. Both chunk first, so an
    export is never a snapshot of a stale corpus.
    """
    from .core.embeddings import (
        EmbeddingError,
        embed_all_pending,
        export_pending,
        import_vectors,
        rebuild_vectors,
    )

    settings = get_settings()
    conn = get_db()

    # Postings ingested before chunking existed, or whose body changed
    # since, have no chunk rows. Building them here means `embed` never
    # reports "0 pending" on a database full of postings.
    chunked = chunk_pending_postings(conn)
    if chunked.pending:
        print(chunked.format())

    if args.export is not None:
        report = export_pending(conn, args.export, settings, limit=args.limit)
        print(report.format())
        return 0

    if args.import_ is not None:
        try:
            taken = import_vectors(conn, args.import_, settings)
        except (EmbeddingError, OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        reset_vectors()
        print(taken.format())
        _build_bm25_index(conn, settings)
        return 0

    def show(done: int, total: int) -> None:
        """Overwrite one line with the count. A local run takes minutes."""
        end = "\n" if done >= total else ""
        print(f"\r  embedded {done:,}/{total:,}", end=end, flush=True)

    try:
        if args.compact:
            before, after = rebuild_vectors(conn, settings)
            print(f"compacted vectors.npy: {before:,} -> {after:,} row(s)")
            reset_vectors()

        print(f"provider: {settings.embedding_provider} · {settings.embedding_model}")
        report = embed_all_pending(conn, settings=settings, progress=show)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except EmbeddingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    reset_vectors()
    print(report.format())

    chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    if chunks == 0:
        print("")
        print("The chunks table is empty, so there was nothing to embed.")
        print("There are no postings to chunk either — run `cli ingest` first.")
        return 0

    _build_bm25_index(conn, settings)
    return 0


def _build_bm25_index(conn: sqlite3.Connection, settings: Settings) -> None:
    """Bring the keyword index up to date, here rather than at the first search.

    Without this the cost lands on whoever runs the next query, which on a full
    corpus is a minute of a dashboard looking broken. It is a no-op when the
    index already describes the chunks table.
    """
    from .core.bm25_index import get_or_build

    index = get_or_build(conn, settings)
    reset_bm25_index()
    print(f"keyword index: {index.n_docs:,} chunk(s), {len(index.terms):,} term(s)")


def cmd_status(_args: argparse.Namespace) -> int:
    """Show the pipeline: what is in the database and where it stands."""
    settings = get_settings()
    conn = get_db()
    summary = stats(conn)

    if summary["total"] == 0:
        print(f"No postings yet in {settings.db_path}")
        print("Fetch some with:  cli ingest")
        return 0

    print(f"{summary['total']:,} postings  ·  {settings.db_path}")

    print("\nby status")
    for status, count in summary["by_status"].items():
        bar = "█" * min(40, round(count / max(summary["total"], 1) * 40)) or "▏"
        print(f"  {status:16} {count:6,}  {bar}")

    print("\nby level")
    for level, count in sorted(summary["by_level"].items(), key=lambda kv: -kv[1]):
        print(f"  {level:16} {count:6,}")

    print("\nby source")
    for source, count in sorted(summary["by_source"].items(), key=lambda kv: -kv[1]):
        print(f"  {source:16} {count:6,}")

    print("\ntop companies")
    for row in summary["by_company"][:10]:
        interns = f"{row['intern']:>4} intern" if row["intern"] else ""
        print(f"  {row['company']:24} {row['count']:6,}  {interns}")

    chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    embedded = conn.execute("SELECT count(*) FROM chunks WHERE vector_row IS NOT NULL").fetchone()[
        0
    ]
    print(f"\nchunks: {chunks:,}  ({embedded:,} embedded)")
    if chunks == 0:
        print("  no chunks yet — run `cli embed` to chunk and embed the postings")

    from .inbox import pending_count

    waiting = pending_count(conn)
    if waiting:
        print(f"\ninbox: {waiting} email suggestion(s) waiting for review")
        print("  review them at /inbox, or run: cli sync-email")

    recent = conn.execute(
        "SELECT posting_id, from_status, to_status, changed_at FROM status_history "
        "ORDER BY id DESC LIMIT 5"
    ).fetchall()
    if recent:
        print("\nrecent changes")
        for row in recent:
            print(
                f"  {row['changed_at'][:10]}  {row['posting_id']:28} "
                f"{row['from_status'] or 'untriaged'} → {row['to_status']}"
            )
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Measure retrieval against the labelled query set."""
    from .core.evaluate import load_eval_set, run_eval

    settings = get_settings()
    settings.ensure_dirs()
    path = Path(args.path) if args.path else settings.eval_dir / "queries.jsonl"

    try:
        queries = load_eval_set(path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"{len(queries)} labelled queries from {path}")
    try:
        result = run_eval(queries)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(result.format())
    return 0


def cmd_sync_email(args: argparse.Namespace) -> int:
    """Read application replies out of Gmail and suggest status changes.

    Never changes an application. Suggestions are reviewed in the dashboard.
    """
    from .inbox import (
        GmailError,
        NotAuthorised,
        authorize,
        build_client,
        list_suggestions,
        save_token,
        sync_email,
    )

    settings = get_settings()
    settings.ensure_dirs()
    conn = get_db()

    if args.login:
        client_id, client_secret = settings.require_google_client()
        try:
            token = authorize(client_id, client_secret)
        except GmailError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        save_token(settings.gmail_token_path, token)
        print(f"authorised, read-only. Token stored at {settings.gmail_token_path}")
        if args.login_only:
            return 0

    try:
        client = build_client(settings)
    except NotAuthorised as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        report = sync_email(
            conn, settings, client, limit=args.limit, include_sent=args.include_sent
        )
    except GmailError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(report.format())

    pending = list_suggestions(conn, pending_only=True, actionable_only=True)
    if pending:
        print()
        print("waiting for review:")
        for row in pending[:20]:
            target = row["posting_id"] or f"(unmatched, looks like {row['company_guess']})"
            confidence = row["confidence"] or 0.0
            print(
                f"  [{row['id']:>4}] {row['classification']:9} {confidence:.2f}  "
                f"-> {row['suggested_status']:12} {target}"
            )
            print(f"         {row['subject'][:88]}")
        if len(pending) > 20:
            print(f"  ... and {len(pending) - 20} more")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    """A REPL over the agent loop."""
    from .core.agent import DoneEvent, TextEvent, ToolCallEvent, ToolResultEvent, run_agent
    from .core.tools import descriptions_written

    if not descriptions_written():
        print("warning: the tool descriptions are still placeholders, so the model")
        print("         will choose tools badly. See core/tools.py TOOL_SCHEMAS.\n")

    history: list[dict[str, object]] = []
    print("Ask a question. Ctrl-C or an empty line to quit.\n")

    while True:
        try:
            message = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not message:
            return 0

        try:
            for event in run_agent(message, history, args.max_iters):
                if isinstance(event, ToolCallEvent):
                    print(f"  ▸ {event.name} {event.input}")
                elif isinstance(event, ToolResultEvent):
                    print(f"    └ {event.ms}ms")
                elif isinstance(event, TextEvent):
                    print(event.delta, end="", flush=True)
                elif isinstance(event, DoneEvent):
                    print(f"\n  [{event.result.iters} iterations]\n")
                    history = event.result.history
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2


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

    p_letter = sub.add_parser(
        "draft-letter",
        help="draft a motivational letter for one posting",
        description=(
            "Retrieve the most relevant pieces of your own project write-ups and "
            "draft a letter grounded in them. Any fact the model was not given "
            "becomes a [TODO: ...] marker rather than an invention."
        ),
    )
    p_letter.add_argument("posting_id", help='e.g. "greenhouse:4012345"')
    p_letter.add_argument(
        "--chunks",
        type=int,
        default=3,
        help="how many profile extracts to ground the letter in (default 3)",
    )
    p_letter.set_defaults(func=cmd_draft_letter)

    p_profile = sub.add_parser(
        "ingest-profile",
        help="chunk and embed your project write-ups in profile/",
        description=(
            "Read every markdown file in profile/, chunk it, and embed the "
            "chunks. These write-ups are the only facts the letter drafter "
            "knows about you. README.md and example-project.md are skipped."
        ),
    )
    p_profile.set_defaults(func=cmd_ingest_profile)

    p_embed = sub.add_parser(
        "embed",
        help="embed any chunks that do not have a vector yet",
        description=(
            "Find chunks with no vector_row, embed them in batches, append to "
            "data/vectors.npy and write the row indices back. Safe to re-run: "
            "already-embedded chunks are skipped and repeated text is served "
            "from the on-disk cache. With --export and --import the embedding "
            "itself happens on another machine; see cluster/README.md."
        ),
    )
    p_embed.add_argument(
        "--compact",
        action="store_true",
        help="first rebuild vectors.npy, dropping rows no chunk references any more",
    )
    elsewhere = p_embed.add_mutually_exclusive_group()
    elsewhere.add_argument(
        "--export",
        type=Path,
        metavar="FILE.jsonl",
        help="write the pending chunks to a file to embed on another machine, and stop",
    )
    p_embed.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="with --export, write only the first N pending chunks (a dry run of the round trip)",
    )
    elsewhere.add_argument(
        "--import",
        dest="import_",
        type=Path,
        metavar="FILE.npz",
        help="take in vectors embedded on another machine (see cluster/README.md)",
    )
    p_embed.set_defaults(func=cmd_embed)

    p_status = sub.add_parser(
        "status",
        help="show the pipeline: counts by status, level, source and company",
        description="Summarise what is in the database and where each posting stands.",
    )
    p_status.set_defaults(func=cmd_status)

    p_eval = sub.add_parser(
        "eval",
        help="measure retrieval against the labelled query set",
        description=(
            "Run every labelled query through search and report mean recall at each k. "
            "Needs data/eval/queries.jsonl, one JSON object per line: "
            '{"query": "...", "relevant_posting_ids": ["source:id", ...]}'
        ),
    )
    p_eval.add_argument("--path", help="eval set to use (default data/eval/queries.jsonl)")
    p_eval.set_defaults(func=cmd_eval)

    p_sync = sub.add_parser(
        "sync-email",
        help="read application replies from Gmail and suggest status changes",
        description=(
            "Fetch messages received since your earliest application, match them to "
            "postings, and classify each as a rejection, an interview invitation, an "
            "offer, or something else. Read-only: this command never changes an "
            "application. Every result is a suggestion you accept or dismiss in the "
            "dashboard at /inbox, and accepting one records which email caused it."
        ),
    )
    p_sync.add_argument(
        "--login",
        action="store_true",
        help="run the Google authorisation flow first (needed once, and after a revoke)",
    )
    p_sync.add_argument(
        "--login-only",
        action="store_true",
        help="with --login, stop after authorising instead of syncing",
    )
    p_sync.add_argument(
        "--include-sent",
        action="store_true",
        help=(
            "also read mail you sent yourself. Off by default because your own "
            "application to a company would be read as that company's answer; "
            "on, it lets you test the pipeline without a second mailbox."
        ),
    )
    p_sync.add_argument(
        "--limit",
        type=int,
        default=200,
        help="most messages to examine in one run (default 200)",
    )
    p_sync.set_defaults(func=cmd_sync_email)

    p_chat = sub.add_parser(
        "chat",
        help="a REPL over the agent loop",
        description="Ask the agent questions from the terminal, with its tool calls shown.",
    )
    p_chat.add_argument("--max-iters", type=int, default=12, help="tool-use rounds (default 12)")
    p_chat.set_defaults(func=cmd_chat)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    # Windows consoles default to cp1252, which cannot encode the bar glyphs
    # `status` prints. Without this the command dies with UnicodeEncodeError
    # partway through its own output. `errors="replace"` means a console that
    # still cannot render a glyph shows a placeholder rather than crashing.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

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
