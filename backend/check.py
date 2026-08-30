#!/usr/bin/env python3
"""Scoreboard for the nine reserved functions.

    uv run python check.py          # what passes, what is left
    uv run python check.py 3        # run only problem 3
    uv run python check.py 3 -v     # ...with full pytest output

The exercise tests live in `exercises/` and are deliberately outside the main
suite, so `uv run pytest` and CI stay green while you work through them.
The problem statements are in `exercises/README.md`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent

# problem number -> (name, file, pytest -k filter or None)
PROBLEMS: dict[int, tuple[str, str, str | None]] = {
    1: ("chunk_posting", "exercises/test_1_chunking.py", "not profile_doc"),
    2: ("chunk_profile_doc", "exercises/test_1_chunking.py", "profile_doc"),
    3: ("dense_scores", "exercises/test_2_retrieval.py", "dense"),
    4: ("bm25_scores", "exercises/test_2_retrieval.py", "bm25"),
    5: ("fuse", "exercises/test_2_retrieval.py", "fuse"),
    6: ("search", "exercises/test_2_retrieval.py", "search or component_scores"),
    7: ("run_agent", "exercises/test_3_agent.py", "not description"),
    8: ("tool descriptions", "exercises/test_3_agent.py", "description"),
    9: ("recall_at_k + run_eval", "exercises/test_4_evaluate.py", None),
}


def run(problem: int, verbose: bool) -> tuple[int, int, int]:
    """Run one problem's tests. Returns (passed, failed, errored)."""
    _name, path, filter_expr = PROBLEMS[problem]
    # `-o addopts=` clears the `-q` in pyproject.toml. Left in place it combines
    # with the `-q` below into `-qq`, which suppresses the count line this parses.
    cmd = [sys.executable, "-m", "pytest", path, "-p", "no:cacheprovider", "-o", "addopts="]
    if filter_expr:
        cmd += ["-k", filter_expr]
    cmd += ["-v"] if verbose else ["-q", "--no-header", "--tb=no"]

    result = subprocess.run(cmd, cwd=BACKEND, capture_output=not verbose, text=True)
    if verbose:
        return (0, 0, result.returncode)

    output = result.stdout or ""
    passed = failed = errors = 0
    for count, word in re.findall(r"(\d+) (passed|failed|errors?)", output):
        if word == "passed":
            passed = int(count)
        elif word == "failed":
            failed = int(count)
        else:
            errors = int(count)
    return (passed, failed, errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("problem", nargs="?", type=int, choices=sorted(PROBLEMS))
    parser.add_argument("-v", "--verbose", action="store_true", help="show pytest output")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if args.problem:
        name, path, _ = PROBLEMS[args.problem]
        print(f"Problem {args.problem}: {name}  ({path})\n")
        passed, failed, errors = run(args.problem, args.verbose)
        if args.verbose:
            return errors
        total = passed + failed + errors
        if failed or errors:
            print(f"  {passed}/{total} passing")
            print(f"\n  See what is failing:  uv run python check.py {args.problem} -v")
            print("  The problem statement: exercises/README.md")
            return 1
        print(f"  all {passed} passing")
        return 0

    print("The nine reserved functions\n")
    done = 0
    results: dict[int, tuple[int, int, int]] = {}
    for number in sorted(PROBLEMS):
        name, _path, _ = PROBLEMS[number]
        passed, failed, errors = run(number, False)
        results[number] = (passed, failed, errors)
        total = passed + failed + errors
        if total == 0:
            mark, detail = "?", "could not run"
        elif failed or errors:
            mark, detail = " ", f"{passed}/{total}"
        else:
            mark, detail = "x", f"{passed}/{total}"
            done += 1
        print(f"  [{mark}] {number}. {name:24} {detail}")

    print(f"\n  {done}/9 done")
    if done < len(PROBLEMS):
        nxt = next(
            n for n in sorted(PROBLEMS) if results[n][1] or results[n][2] or sum(results[n]) == 0
        )
        print(f"  next: uv run python check.py {nxt} -v")
    else:
        print("\n  All nine. Now: cli embed, cli eval, and see what the numbers say.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
