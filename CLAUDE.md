# Working agreement

Read PLAN.md before doing anything. It is the spec.

Then read PROGRESS.md. It is the log: what is already built, what is next, and
the decisions made in conversation that amend PLAN.md. Where the two conflict,
PROGRESS.md's "Amendments to plan.md" section wins — those were agreed with the
author and are not mistakes to correct.

Two rules that override everything else:

1. Category B functions (listed in PLAN.md) must stay as
   `raise NotImplementedError`. Never implement them, never provide a
   "temporary" version, never inline the logic elsewhere.

   **One exception, added 2026-08-30 by the author.** Once the author says they
   have finished writing a Category B function themselves, they may ask for it
   to be rewritten. Then, and only then, replace it with your own best version
   and explain what you changed and why. Until the author says a function is
   done, the rule above is absolute — do not offer, hint at, or sketch an
   implementation, including in chat.

2. Do one phase per session. Stop at the phase check and report. Do not
   continue to the next phase without being asked.

Update PROGRESS.md at the end of every phase, and whenever the author says
"checkpoint". Keep it short: status, decisions, and what the next session needs
to know. It is not a changelog — git already has that.

## The author is working through the Category B functions

Nine of them, as a set of exercises. Everything else in the project is built.

- Problem statements: `backend/exercises/README.md`
- Scoreboard: `cd backend && uv run python check.py`
- Chunking playground: `cd backend && uv run python try_chunking.py`

The exercise tests live in `backend/exercises/`, outside the main suite, so
`uv run pytest` and CI stay green while the author works. Do not move them into
`tests/`, and do not "fix" a failing exercise test by writing the function.

Helping is teaching, not typing: explain the concept, show real data, point at
the failing assertion, review code the author has written. Answer questions
about what a function should do as fully as asked.
