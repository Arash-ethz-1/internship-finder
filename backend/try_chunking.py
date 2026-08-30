#!/usr/bin/env python3
"""A scratch harness for working on `chunk_posting`.

Run it, look at the output, change `core/chunking.py`, run it again. That loop
is the whole point: chunking is a judgement call, and the only way to make it
is to keep looking at what your rule actually produces on real postings.

    cd backend
    uv run python try_chunking.py                 # a random intern posting
    uv run python try_chunking.py --id lever:abc  # one specific posting
    uv run python try_chunking.py --long          # the longest posting there is
    uv run python try_chunking.py --all           # health check over 200 postings
    uv run python try_chunking.py --show-body     # print the raw body first

This file is a tool, not part of the app. Delete it when you are done.
"""

from __future__ import annotations

import argparse
import statistics
import sys

from agent_app.core.chunking import chunk_posting
from agent_app.db import Posting
from agent_app.runtime import get_db

RULE = "-" * 74


def pick(args: argparse.Namespace) -> list[Posting]:
    conn = get_db()
    if args.id:
        row = conn.execute("SELECT * FROM postings WHERE id = ?", (args.id,)).fetchone()
        if row is None:
            sys.exit(f"No posting with id {args.id!r}")
        return [Posting.from_row(row)]

    if args.all:
        rows = conn.execute(
            "SELECT * FROM postings ORDER BY random() LIMIT ?", (args.count,)
        ).fetchall()
        return [Posting.from_row(r) for r in rows]

    order = "length(body) DESC" if args.long else "random()"
    where = "" if args.any_level else "WHERE level = 'intern'"
    row = conn.execute(f"SELECT * FROM postings {where} ORDER BY {order} LIMIT 1").fetchone()
    if row is None:
        sys.exit("No postings in the database. Run: uv run python -m agent_app.cli ingest")
    return [Posting.from_row(row)]


def show_one(posting: Posting, max_chars: int, show_body: bool) -> None:
    print(RULE)
    print(f"{posting.company} - {posting.title}")
    print(f"{posting.id}   body is {len(posting.body):,} chars")
    print(RULE)

    if show_body:
        print(posting.body)
        print(RULE)

    chunks = chunk_posting(posting, max_chars)
    print(f"-> {len(chunks)} chunk(s)\n")

    for chunk in chunks:
        head = f"[{chunk.ordinal}] {len(chunk.text):,} chars"
        print(head)
        print("  " + chunk.text.replace("\n", "\n  "))
        print()

    check(chunks, max_chars)


def check(chunks: list, max_chars: int) -> list[str]:
    """The contract from the docstring, checked out loud."""
    problems: list[str] = []
    if not chunks:
        problems.append("no chunks produced")
    if [c.ordinal for c in chunks] != list(range(len(chunks))):
        problems.append("ordinals are not 0, 1, 2, ... with no gaps")
    if any(not c.text.strip() for c in chunks):
        problems.append("a chunk is empty or only whitespace")
    over = [c.ordinal for c in chunks if len(c.text) > max_chars]
    if over:
        problems.append(f"chunk(s) {over} are longer than max_chars={max_chars}")

    if problems:
        print("PROBLEMS:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("contract holds: ordinals contiguous, none empty, none over max_chars")
    return problems


def survey(postings: list[Posting], max_chars: int) -> None:
    """Run over many postings and report the shape of the result."""
    sizes: list[int] = []
    counts: list[int] = []
    failures = 0

    for posting in postings:
        try:
            chunks = chunk_posting(posting, max_chars)
        except Exception as exc:  # noqa: BLE001 - a survey should not stop at one
            failures += 1
            print(f"  FAILED {posting.id}: {type(exc).__name__}: {exc}")
            continue
        if check_quiet(chunks, max_chars):
            failures += 1
            print(f"  CONTRACT BROKEN {posting.id} ({posting.company})")
        counts.append(len(chunks))
        sizes.extend(len(c.text) for c in chunks)

    print(RULE)
    print(f"{len(postings)} postings, {failures} problem(s)")
    if not sizes:
        return
    median_count = statistics.median(counts)
    median_size = statistics.median(sizes)
    print(f"chunks per posting : min {min(counts)}  median {median_count:.0f}  max {max(counts)}")
    print(f"chunk size (chars) : min {min(sizes)}  median {median_size:.0f}  max {max(sizes)}")
    tiny = sum(1 for s in sizes if s < 100)
    print(f"chunks under 100 chars: {tiny} of {len(sizes)}  ({tiny / len(sizes):.0%})")
    print("\nA lot of tiny chunks usually means you are splitting on every blank")
    print("line without packing small pieces back together.")


def check_quiet(chunks: list, max_chars: int) -> bool:
    return bool(
        not chunks
        or [c.ordinal for c in chunks] != list(range(len(chunks)))
        or any(not c.text.strip() for c in chunks)
        or any(len(c.text) > max_chars for c in chunks)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", help="a specific posting id")
    parser.add_argument("--long", action="store_true", help="use the longest posting")
    parser.add_argument("--any-level", action="store_true", help="do not restrict to interns")
    parser.add_argument("--show-body", action="store_true", help="print the raw body first")
    parser.add_argument("--all", action="store_true", help="survey many postings")
    parser.add_argument("--count", type=int, default=200, help="how many, with --all")
    parser.add_argument("--max-chars", type=int, default=1200)
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    postings = pick(args)
    try:
        if args.all:
            survey(postings, args.max_chars)
        else:
            show_one(postings[0], args.max_chars, args.show_body)
    except NotImplementedError:
        print(RULE)
        print("chunk_posting still raises NotImplementedError.")
        print("Open backend/src/agent_app/core/chunking.py and write the body.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
