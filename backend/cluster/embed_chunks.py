#!/usr/bin/env python3
"""Embed an exported chunk file on a machine that is faster than the laptop.

This script is deliberately standalone. It does not import ``agent_app``, does
not open the database, and needs nothing from this repository except itself:
copy it and the exported ``.jsonl`` to wherever the compute is, run it, copy
the ``.npz`` back. Its only dependencies are ``fastembed`` and ``numpy``.

    # here
    uv run python -m agent_app.cli embed --export data/pending.jsonl

    # there
    python embed_chunks.py pending.jsonl --out vectors.npz

    # here again
    uv run python -m agent_app.cli embed --import data/vectors.npz

The model name is read from the export's header line rather than passed as an
argument. Two machines disagreeing about which model wrote a vector is the one
mistake that produces no error at all -- search simply gets worse -- so the
question is never asked twice.

Same library on both ends on purpose. ``fastembed`` pins the tokenizer, the
pooling and the normalisation together with the weights, so a vector produced
here is the vector the laptop would have produced, only sooner. Reimplementing
that with a different library is how vector spaces drift apart.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

# Some models were trained with a fixed instruction glued to the front of every
# text. Kept in step with LOCAL_MODEL_PREFIXES in core/embeddings.py: if these
# two ever disagree, the exported vectors stop matching the query vectors.
MODEL_PREFIXES: dict[str, str] = {
    "intfloat/multilingual-e5-large": "query: ",
}


def read_export(path: Path) -> tuple[dict, list[int], list[str]]:
    """Return the header, the chunk ids and their text, in file order."""
    ids: list[int] = []
    texts: list[str] = []

    with path.open(encoding="utf-8") as handle:
        first = handle.readline()
        if not first.strip():
            raise SystemExit(f"{path} is empty")
        header = json.loads(first)
        if "model" not in header or "dim" not in header:
            raise SystemExit(
                f"{path} has no header line naming the model. "
                "It should be the file written by `cli embed --export`."
            )
        for line_number, line in enumerate(handle, start=2):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                ids.append(int(record["id"]))
                texts.append(record["text"])
            except (ValueError, KeyError) as exc:
                raise SystemExit(f"{path}:{line_number} is not a chunk record: {exc}") from exc

    return header, ids, texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Embed a chunk export and write an .npz for `cli embed --import`.",
    )
    parser.add_argument("export", type=Path, help="the .jsonl written by `cli embed --export`")
    parser.add_argument("--out", type=Path, required=True, help="where to write the .npz")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=256,
        help="texts per forward pass (default: 256; raise it on a GPU)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="ONNX Runtime threads (default: let it decide; set it to the job's --cpus-per-task)",
    )
    parser.add_argument(
        "--cuda",
        action="store_true",
        help="require a GPU. Needs onnxruntime-gpu, and exits if CUDA did not load.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="where to keep the downloaded model (a login node's temp dir is cleared)",
    )
    args = parser.parse_args(argv)

    header, ids, texts = read_export(args.export)
    model_name = str(header["model"])
    dim = int(header["dim"])
    prefix = MODEL_PREFIXES.get(model_name, "")

    print(f"{len(ids):,} chunk(s) from {args.export}", flush=True)
    print(f"model {model_name}, dim {dim}, prefix {prefix!r}", flush=True)

    if not ids:
        np.savez(
            args.out,
            ids=np.zeros(0, dtype=np.int64),
            vectors=np.zeros((0, dim), dtype=np.float32),
            model=model_name,
            dim=dim,
        )
        print(f"nothing to embed; wrote an empty {args.out}", flush=True)
        return 0

    from fastembed import TextEmbedding

    embedder = TextEmbedding(
        model_name=model_name,
        cache_dir=str(args.cache_dir) if args.cache_dir else None,
        threads=args.threads,
        cuda=args.cuda or False,
    )

    # fastembed only *warns* when CUDA fails to load and then quietly runs on
    # the CPU, which on a GPU node means an hour of holding a card you are not
    # using. --cuda has to mean it.
    providers = getattr(getattr(embedder.model, "model", None), "get_providers", list)()
    print(f"execution providers: {', '.join(providers) or 'unknown'}", flush=True)
    if args.cuda and not any("CUDA" in provider for provider in providers):
        raise SystemExit(
            "--cuda was requested but ONNX Runtime is running on "
            f"{', '.join(providers) or 'the CPU'}. The reason is in the job's .err file, "
            "usually a CUDA/cuDNN version mismatch. Either fix onnxruntime-gpu "
            "(see cluster/README.md) or drop --cuda and run on the CPU nodes."
        )

    prepared = [prefix + text for text in texts] if prefix else texts

    started = time.perf_counter()
    vectors = np.zeros((len(ids), dim), dtype=np.float32)
    done = 0
    # Written into a preallocated array rather than collected in a list: at
    # 135,000 chunks the list of small arrays costs more than the result.
    for vector in embedder.embed(prepared, batch_size=args.batch_size):
        vectors[done] = vector
        done += 1
        if done % 2000 == 0 or done == len(ids):
            rate = done / (time.perf_counter() - started)
            left = (len(ids) - done) / rate / 60
            print(f"  {done:,}/{len(ids):,}  {rate:,.0f}/s  {left:,.1f} min left", flush=True)

    if vectors.shape != (len(ids), dim):
        raise SystemExit(
            f"the model returned {vectors.shape}, but the export asked for {(len(ids), dim)}. "
            "The header's dim and the model disagree."
        )
    if not np.isfinite(vectors).all():
        raise SystemExit("the model produced NaN or infinity; refusing to write the file")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        args.out,
        ids=np.asarray(ids, dtype=np.int64),
        vectors=vectors,
        model=model_name,
        dim=dim,
    )
    elapsed = time.perf_counter() - started
    size_mb = args.out.stat().st_size / 1e6
    print(
        f"wrote {args.out} — {len(ids):,} vectors, {size_mb:,.0f} MB, {elapsed / 60:,.1f} min",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
