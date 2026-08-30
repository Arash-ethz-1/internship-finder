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
2. Do one phase per session. Stop at the phase check and report. Do not
   continue to the next phase without being asked.

Update PROGRESS.md at the end of every phase, and whenever the author says
"checkpoint". Keep it short: status, decisions, and what the next session needs
to know. It is not a changelog — git already has that.