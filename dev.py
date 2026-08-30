#!/usr/bin/env python3
"""Start both servers with one command.

PLAN.md asks for a Makefile or justfile, but neither ``make`` nor ``just`` is
installed on this machine, and both are an extra install on Windows. Python is
already a hard requirement, so the task runner is a Python script.

    python dev.py            # API + Vite, streaming both logs
    python dev.py api        # API only
    python dev.py web        # Vite only
    python dev.py ingest     # fetch postings from every verified board
    python dev.py embed      # embed any chunks that have no vector yet
    python dev.py status     # what is in the database
    python dev.py test       # ruff check + pytest
    python dev.py lint       # ruff check + ruff format --check + tsc + eslint

Ctrl-C stops everything.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

NPM = "npm.cmd" if os.name == "nt" else "npm"

API_CMD = ["uv", "run", "uvicorn", "agent_app.api.main:app", "--reload", "--port", "8000"]
WEB_CMD = [NPM, "run", "dev"]


def _stream(proc: subprocess.Popen[str], label: str) -> None:
    """Prefix every line of a child's output so two logs can share one terminal."""
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(f"[{label}] {line}")
        sys.stdout.flush()


def run_many(specs: list[tuple[str, list[str], Path]]) -> int:
    """Run processes concurrently until one dies or Ctrl-C arrives."""
    procs: list[subprocess.Popen[str]] = []
    threads: list[threading.Thread] = []

    for label, cmd, cwd in specs:
        print(f"[dev] starting {label}: {' '.join(cmd)} (in {cwd.name}/)")
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        procs.append(proc)
        thread = threading.Thread(target=_stream, args=(proc, label), daemon=True)
        thread.start()
        threads.append(thread)

    code = 0
    try:
        while True:
            for proc in procs:
                ret = proc.poll()
                if ret is not None:
                    print(f"[dev] a process exited with {ret}; shutting down")
                    code = ret
                    raise KeyboardInterrupt
            for thread in threads:
                thread.join(timeout=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return code


def run_sequence(specs: list[tuple[str, list[str], Path]]) -> int:
    """Run commands one after another, stopping at the first failure."""
    for label, cmd, cwd in specs:
        print(f"[dev] {label}: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=cwd)
        if result.returncode != 0:
            return result.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev.py", description=__doc__)
    parser.add_argument(
        "task",
        nargs="?",
        default="all",
        choices=["all", "api", "web", "test", "lint", "ingest", "embed", "status"],
        help="what to run (default: all)",
    )
    args = parser.parse_args(argv)

    if args.task == "api":
        return run_many([("api", API_CMD, BACKEND)])
    if args.task == "web":
        return run_many([("web", WEB_CMD, FRONTEND)])
    if args.task == "test":
        return run_sequence(
            [
                ("ruff", ["uv", "run", "ruff", "check", "."], BACKEND),
                ("pytest", ["uv", "run", "pytest"], BACKEND),
            ]
        )
    if args.task == "lint":
        return run_sequence(
            [
                ("ruff check", ["uv", "run", "ruff", "check", "."], BACKEND),
                ("ruff format", ["uv", "run", "ruff", "format", "--check", "."], BACKEND),
                ("tsc", [NPM, "run", "typecheck"], FRONTEND),
                ("eslint", [NPM, "run", "lint"], FRONTEND),
            ]
        )

    if args.task in {"ingest", "embed", "status"}:
        return run_sequence(
            [
                (
                    args.task,
                    ["uv", "run", "python", "-m", "agent_app.cli", args.task],
                    BACKEND,
                )
            ]
        )

    return run_many([("api", API_CMD, BACKEND), ("web", WEB_CMD, FRONTEND)])


if __name__ == "__main__":
    raise SystemExit(main())
