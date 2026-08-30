# The nine functions

Nine problems, in dependency order. Each one is a real function in `core/` that
the rest of the app already calls.

## How to work

```bash
cd backend
uv run python check.py            # the scoreboard: what passes, what is left
uv run python check.py 1          # run only problem 1's tests
uv run python check.py 1 -v       # ...with the full failure output
```

These tests live outside the main suite on purpose, so `uv run pytest` and CI
stay green while you work.

Passing every test does **not** mean your answer is good — only that it is not
broken. Chunking especially has no test that can judge it; that is what
`try_chunking.py` and later `cli eval` are for.

---

## 1. `chunk_posting`

**File:** `core/chunking.py`

Split a job posting into pieces small enough to embed individually.

```python
def chunk_posting(posting: Posting, max_chars: int = 1200) -> list[Chunk]
```

**Input.** A `Posting`. You care about `.body` (plain text; paragraphs
separated by `\n\n`, bullets prefixed with `- `), and optionally `.title` and
`.company`.

**Output.** A list of `Chunk(text=..., ordinal=...)`.

**Must hold**
- `ordinal` is 0, 1, 2, … in document order, no gaps
- no chunk is empty or whitespace-only
- `len(chunk.text) <= max_chars` for every chunk
- calling it twice on the same posting gives the same result

**Example** — body of 3 paragraphs (900, 200, 150 chars), `max_chars=1200`:
a reasonable answer is 2 chunks — `[900]` then `[200 + 150]` — because packing
the two small ones together beats emitting them as separate fragments.

**Yours to decide:** whether to prepend title/company to each chunk, whether
chunks overlap, whether to drop company boilerplate, and the smallest chunk
worth keeping.

**Hint if stuck:** get *anything* working first — slice the body into
fixed-size pieces, run `try_chunking.py`, then improve. Do not design it in
your head.

---

## 2. `chunk_profile_doc`

**File:** `core/chunking.py`

```python
def chunk_profile_doc(text: str, max_chars: int = 1200) -> list[Chunk]
```

Same contract as #1, but the input is the raw markdown of one of your project
write-ups, and there is no `Posting` wrapper.

Markdown headings (`## What I built`) are the natural boundary. A chunk that
loses which section it came from produces a letter that attributes your work to
the wrong project.

---

## 3. `dense_scores`

**File:** `core/retrieval.py`

Score every stored vector against the query vector by **cosine similarity**.

```python
def dense_scores(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray
```

**Input.** `query_vec` shape `(dim,)`. `matrix` shape `(n, dim)`.
**Output.** Shape `(n,)`. Higher means more similar.

Cosine similarity is the dot product divided by the product of the two
magnitudes — the cosine of the angle between them. It ignores length and
measures direction only, which is what you want: a long document and a short
query about the same thing should score high.

```
cos(a, b) = (a · b) / (‖a‖ · ‖b‖)
```

**Must hold**
- a vector compared against itself scores 1.0
- doubling a vector's magnitude does not change its score (this is the test
  that catches a plain dot product)
- opposite directions score -1.0, perpendicular score 0.0
- an empty matrix returns an empty array, it does not crash
- a zero vector does not produce `nan`

**This is the easiest of the nine.** Numpy does the work; the whole body is a
few lines. Start here if you want a win before tackling chunking.

---

## 4. `bm25_scores`

**File:** `core/retrieval.py`

Keyword scoring. Where dense search compares meaning, this counts words — and
is the half that reliably finds an exact term like "PyTorch".

```python
def bm25_scores(query: str, corpus_tokens: list[list[str]]) -> np.ndarray
```

**Input.** The raw query string, and one token list per candidate document
(build query tokens with `tokenize`, already written for you).
**Output.** Shape `(len(corpus_tokens),)`.

For each query term `t` and document `d`:

```
score(d) = Σ  IDF(t) · ( f(t,d) · (k1 + 1) ) / ( f(t,d) + k1 · (1 - b + b · |d|/avgdl) )
          t∈q

IDF(t) = ln( 1 + (N - n(t) + 0.5) / (n(t) + 0.5) )
```

- `f(t,d)` — how many times `t` appears in `d`
- `|d|` — length of `d` in tokens; `avgdl` — mean document length
- `N` — number of documents; `n(t)` — how many contain `t`
- `k1 ≈ 1.5` controls saturation (the 10th occurrence adds less than the 2nd)
- `b ≈ 0.75` controls length normalisation (long documents get discounted)

**Must hold**
- a document containing a query term never scores below one containing none
- a rarer term contributes more than a common one
- term frequency saturates — 20 occurrences is not 10× better than 2
- an empty corpus, or a query matching nothing, returns zeros rather than
  crashing
- no `nan` or `inf` anywhere, including when a term appears in *every*
  document (the textbook IDF can go negative there — decide what to do)

---

## 5. `fuse`

**File:** `core/retrieval.py`

Combine several score arrays into one ranking.

```python
def fuse(score_lists: list[np.ndarray], k: int = 60) -> np.ndarray
```

Every array covers the same candidates in the same order. Returns one fused
score per candidate.

You cannot just add the scores: cosine lives in `[-1, 1]` while BM25 is
unbounded, so BM25 would drown dense out. **Reciprocal Rank Fusion** avoids
this by throwing away the values and keeping only each candidate's *position*:

```
fused(d) = Σ  1 / (k + rank_i(d))
          i
```

where `rank_i(d)` is `d`'s rank in list `i`, best = 1.

**Must hold**
- output length equals input length
- a candidate ranked first in both lists beats one ranked first in only one
- ties in the input do not produce `nan`
- one list in, one ranking out (it still works with a single list)

**Note for #6:** `search` needs each list's contribution separately, and they
must sum to the fused score. With RRF each `1/(k + rank)` term *is* that
contribution, so the decomposition falls out of the same arithmetic. Consider
whether to return it here or recompute it in `search`.

---

## 6. `search`

**File:** `core/retrieval.py`

Glue. Mostly wiring — the pieces are all written.

```python
def search(query: str, filters: SearchFilters, k: int = 10) -> list[SearchHit]
```

1. `conn = get_db()`; `candidates = load_candidates(conn, filters)` *(both ready)*
2. embed `query` with `runtime.get_provider()`
3. `dense_scores` over the candidates' rows of `runtime.get_vectors()`
4. `bm25_scores` over `[tokenize(c.text) for c in candidates]`
5. `fuse` the two
6. top `k` as `SearchHit` objects

**Must hold**
- at most `k` hits, sorted by score descending
- `rank` is 1, 2, 3, … matching that order
- `component_scores` has keys `"dense"` and `"bm25"`, and **the two values sum
  to `score`** — the dashboard draws them as a stacked bar, which is only
  honest if the parts add up
- filters are respected (`load_candidates` does this for you)
- an empty candidate set returns `[]`
- candidates whose `vector_row` is `None` are not embedded yet — decide whether
  to skip them or score them on BM25 alone

---

## 7. `run_agent`

**File:** `core/agent.py`

The tool-use loop. This is the one you said you are here to learn.

```python
def run_agent(user_message, history, max_iters=12) -> Iterator[AgentEvent]
```

A `while` loop around an API call:

1. append `user_message` to `history` as a user message
2. call the model with `TOOL_SCHEMAS` from `core.tools`
3. for each tool the model asks for: `yield ToolCallEvent(...)`, run it through
   `TOOL_FUNCTIONS`, `yield ToolResultEvent(...)` with elapsed ms, append the
   result to `history`
4. `yield TextEvent(...)` for any prose
5. repeat until the model stops asking for tools, or `max_iters`
6. `yield DoneEvent(AgentResult(...))` exactly once, last

**Must hold**
- exactly one `DoneEvent`, and it is the last thing yielded
- a tool that raises is reported back to the model as a tool result, not
  allowed to kill the loop
- hitting `max_iters` still emits a `DoneEvent`
- `history` in the result can be passed straight back in as the next turn's
  `history`

**One constraint for the tests:** obtain the client as
`anthropic.Anthropic(api_key=settings.require_anthropic_key())` so the tests
can substitute a fake model. `core/letters.py` does exactly this if you want a
worked example of the call shape.

---

## 8. The four tool descriptions

**File:** `core/tools.py` — every `"TODO: author writes this"`

Not code. The model never sees your function signatures; these strings are the
entire interface. A vague description produces an agent that searches when it
should have looked something up.

For each of `search_postings`, `get_posting`, `update_status`,
`list_shortlist`, say what it does, when to reach for it *instead of* the
others, and what its arguments mean.

The test only checks that they are no longer placeholders and are not
one-liners. Whether they are *good* shows up when you use `cli chat`.

---

## 9. `recall_at_k` and `run_eval`

**File:** `core/evaluate.py`

```python
def recall_at_k(retrieved: list[str], relevant: list[str], k: int) -> float
def run_eval(queries: list[EvalQuery], k_values=(1, 5, 10, 20)) -> EvalResult
```

`recall_at_k` — what fraction of the relevant items appear in the top `k`
retrieved. Five lines.

**Must hold**
- `retrieved=[a,b,c]`, `relevant=[a,c]`, `k=3` → `1.0`
- `k=1` → `0.5`
- empty `relevant` → decide and be consistent (0.0 is defensible; a crash is
  not)
- duplicates in `retrieved` — several chunks of one posting can match; decide
  whether that counts once
- `k` larger than `len(retrieved)` is fine

`run_eval` runs each query through `search`, collapses chunk hits to their
parent posting (keeping the best rank per posting), and averages
`recall_at_k` across queries.

**This one needs data you write by hand.** `data/eval/queries.jsonl`, one
object per line:

```json
{"query": "remote ML internships in Europe", "relevant_posting_ids": ["greenhouse:123", "lever:abc"]}
```

Twenty to thirty of these. Finding the ids is the boring part — use the
dashboard, filter, and copy them. It is the only way to know whether your
chunking decisions actually helped.
