# Your project write-ups

One markdown file per project you have actually built. These are the **only**
facts the letter drafter knows about you: it retrieves the most relevant pieces
of this folder and is forbidden from inventing anything else. Where it needs a
detail you have not written down, it leaves a `[TODO: ...]` marker instead of
making one up.

So the rule is simple: **the more specific this folder is, the better every
letter gets.** Vague files produce vague letters.

## What is committed and what is not

Everything in here is gitignored except this README and `example-project.md`.
Your real write-ups never reach GitHub, which matters because this repository
is public.

```
profile/
  README.md              committed
  example-project.md     committed
  <anything else>.md     yours, never committed
```

## Format

A file per project, named as a slug. The filename becomes the `profile_doc`
value in the database and is what shows up beside each retrieved extract in the
dashboard, so name it after the project rather than `notes-2.md`.

```
distributed-attention.md
gnn-maze-solver.md
pyblio.md
```

Inside, plain markdown. Headings matter: chunking splits on them, so each
section should stand on its own and still make sense when retrieved without
the rest of the file.

## What to write

Write for a reader who has never seen the project and will only ever read one
section of it.

- **What it does**, in one or two sentences, without jargon
- **What you built**, specifically. Not "worked on the backend": which parts,
  which decisions were yours, what you would do differently
- **The technical detail** an interviewer would follow up on: the algorithm,
  the data structure, the thing that was hard
- **Numbers**, wherever you have them. Dataset size, latency, accuracy,
  how many users. A letter that says "reduced p99 latency from 400ms to 90ms"
  is worth ten that say "improved performance"
- **What went wrong**, and what you did about it

Skip anything you would not be comfortable being asked about in an interview.
Every sentence here is something a letter may quote back at a company.

## After editing

```
cd backend
uv run python -m agent_app.cli ingest-profile
```

That re-chunks and re-embeds this folder. Re-embedding is nearly free: the
on-disk cache is keyed by the text itself, so only what you actually changed
costs anything.
