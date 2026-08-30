# Working agreement

Read PLAN.md before doing anything. It is the spec.

Two rules that override everything else:

1. Category B functions (listed in PLAN.md) must stay as
   `raise NotImplementedError`. Never implement them, never provide a
   "temporary" version, never inline the logic elsewhere.
2. Do one phase per session. Stop at the phase check and report. Do not
   continue to the next phase without being asked.