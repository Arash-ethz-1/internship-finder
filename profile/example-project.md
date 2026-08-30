# Example Project

Delete this file once you have written a real one. It exists to show the shape
and the level of detail, not to be ingested — placeholder text produces
placeholder letters.

## What it does

One or two sentences a non-specialist would understand. What problem does it
solve, and for whom?

> A command-line tool that keeps a BibTeX bibliography in sync with the PDFs on
> disk, so citations never point at a paper you no longer have.

## What I built

Be specific about your own contribution, especially on team projects. Name the
parts that were yours and the decisions you made.

> I wrote the matching layer: it pairs a BibTeX entry to a PDF using DOI first,
> then a normalised title comparison, and refuses to guess when both fail. I
> chose to make the ambiguous case an error rather than a best guess, because a
> silently wrong citation is worse than a tool that asks.

## How it works

The technical detail an interviewer would follow up on. The algorithm, the data
structure, the part that was actually hard.

> Title matching normalises unicode, strips subtitles after a colon, and
> compares with a token-set ratio. The threshold was tuned on 400 hand-labelled
> pairs from my own library. Below 0.85 the tool asks; above it, it links.

## Numbers

Anything measurable. This is what makes a letter concrete instead of generic.

> 1,200 entries, 940 PDFs. 96% matched automatically, 3% flagged as ambiguous,
> 1% missing. A full sync takes 2.1 seconds.

## What went wrong

The honest part. What broke, what you misjudged, what you would change.

> The first version matched on title alone and linked two different papers by
> the same group with nearly identical titles. That is what pushed DOI to the
> front of the chain and made ambiguity an explicit state rather than a
> tie-break.
