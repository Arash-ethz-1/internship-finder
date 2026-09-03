# Embedding somewhere faster

The laptop this project is developed on embeds about **1.7 chunks a second**
with `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. The corpus
is 135,871 chunks. That is roughly **22 hours**, which is not a thing you do
twice, and tuning chunking means doing it repeatedly.

So the bulk embedding goes somewhere else. Nothing else does: queries are
embedded on the laptop at search time, and a single short query takes about
130 ms, which is fine.

What travels is small and one-way in each direction:

```
laptop                              cluster
──────                              ───────
cli embed --export pending.jsonl
        pending.jsonl  ──────────►  embed_chunks.py
                                    (fastembed, same model)
        vectors.npz    ◄──────────  vectors.npz
cli embed --import vectors.npz
```

The database never leaves this machine and the repository never reaches the
cluster. `embed_chunks.py` imports nothing from `agent_app`; it needs only
`fastembed` and `numpy`.

## Why the same library on both ends

A vector is only comparable to another vector from the same model *and the
same pipeline*. Tokenizer, pooling and normalisation are as much a part of it
as the weights. `fastembed` pins all four together, so a vector produced on the
cluster is the vector the laptop would have produced. Reimplementing the
pipeline with `sentence-transformers` on one end would look fine, run fine, and
quietly rank worse.

The model name is carried in the export's header line rather than passed as an
argument, for the same reason: a machine that is never asked cannot answer
wrongly. `cli embed --import` refuses a file whose model or dimension does not
match the configured one, and refuses one containing NaN.

## Setup, once

On `tik42x` (this is installation, not computation, so the login node is fine):

```bash
ssh ETH_USERNAME@tik42x.ethz.ch

# Per the cluster guide, everything lives in net_scratch.
cd /itet-stor/$USER/net_scratch
mkdir -p internship-finder/jobs

conda create --name embed python=3.11 --channel conda-forge
conda activate embed

# fastembed-gpu is the same package built against onnxruntime-gpu. Use plain
# `fastembed` for the CPU nodes.
pip install fastembed-gpu numpy

# The default onnxruntime-gpu wheel is built for CUDA 13, and the TIK nodes
# run CUDA 12. Without this it loads no CUDA provider, warns, and runs on the
# CPU -- see "When the GPU is not actually used" below.
INDEX=https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-12/pypi/simple/
pip install --force-reinstall onnxruntime-gpu --extra-index-url $INDEX

# onnxruntime-gpu links against CUDA libraries it does not ship. These wheels
# provide them; job.sh puts them on LD_LIBRARY_PATH at run time.
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12     nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-cuda-nvrtc-cu12

# Pull the model down once, into net_scratch rather than a temp dir.
python -c "
from fastembed import TextEmbedding
TextEmbedding(
    model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
    cache_dir='/itet-stor/$USER/net_scratch/fastembed_cache',
)"
```

If `pip install` runs out of space, the cluster guide's fix applies:

```bash
TMPDIR="/itet-stor/$USER/net_scratch/tmp/" && mkdir -p "$TMPDIR" && export TMPDIR
```

## Each run

```bash
# 1. On the laptop.
cd backend
uv run python -m agent_app.cli embed --export ../data/pending.jsonl

# 2. Copy up. The export is text and compresses well.
gzip -k ../data/pending.jsonl
scp ../data/pending.jsonl.gz cluster/embed_chunks.py cluster/job.sh \
    ETH_USERNAME@tik42x.ethz.ch:/itet-stor/ETH_USERNAME/net_scratch/internship-finder/

# 3. On tik42x. Edit job.sh first: TODO_USERNAME appears three times.
cd /itet-stor/$USER/net_scratch/internship-finder
gunzip pending.jsonl.gz
chmod +x job.sh
sbatch job.sh pending.jsonl vectors.npz
squeue -u $USER

# 4. Copy back and apply.
scp ETH_USERNAME@tik42x.ethz.ch:/itet-stor/ETH_USERNAME/net_scratch/internship-finder/vectors.npz ../data/
uv run python -m agent_app.cli embed --import ../data/vectors.npz
```

`--export` changes nothing in the database, and `--import` skips chunks that
already have a vector. Exporting twice, importing once, or importing the same
file twice are all safe.

## When the GPU is not actually used

`fastembed` does not stop when CUDA fails to load. It warns, falls back to the
CPU, and carries on -- so a job can hold a GPU for an hour without touching it.
`embed_chunks.py` therefore checks the providers ONNX Runtime actually chose
and exits if `--cuda` was asked for and CUDA is not among them. The first lines
of the job's `.out` say which it got:

    execution providers: CUDAExecutionProvider, CPUExecutionProvider

If it exits, the reason is in the `.err` file. The common one is a version
mismatch:

    Failed to load library libonnxruntime_providers_cuda.so with error:
    libcublasLt.so.13: cannot open shared object file
    Require cuDNN 9.* and CUDA 13.*

That is the CUDA-13 wheel meeting a CUDA-12 node. The fix is the
`--extra-index-url` install in the setup section above.

Once the version matches, the same message can come back naming `.so.12`
instead. That is a different problem: the right wheel is installed but the
CUDA libraries it links against are not on the loader's path. `job.sh` builds
`LD_LIBRARY_PATH` from the `nvidia-*-cu12` wheels for exactly this, so install
those too and check the job's `.out` for the `CUDA libraries:` line.

If it will not cooperate after that, the CPU nodes are the better trade than
an afternoon of CUDA archaeology. Measured on `artongpu01` with 8 threads the
CPU path did 5 chunks/s, so a 20-core `arton` node should land near an hour --
worse than a GPU, far better than the laptop's 22.

## CPU nodes instead

The cluster guide is explicit that work without a GPU belongs on
`arton[01-08]`, and this job does not need one: a dual-deca-core Xeon is
already twenty-something times this laptop. Drop the GPU lines from `job.sh`:

```bash
#SBATCH --cpus-per-task=16
#SBATCH --nodelist=arton[01-08]
# delete --gres and --exclude
```

and remove `--cuda` from the `python` line. Install plain `fastembed` rather
than `fastembed-gpu` in that case.

## Cluster etiquette

From the guide, and worth repeating because this job is small enough to be
tempting to run carelessly:

- **Never compute on `tik42x`.** Installing the environment there is fine;
  running the embedding is not. Use `sbatch`, or `srun --pty bash -i` for a
  short interactive session while debugging.
- One GPU is enough. This job asks for one, and for less than half of any
  node's CPUs and memory, so it needs no calendar entry and no reservation.
  Those are only required past 4 GPUs, or half a node's cores or memory.
- Access requires being listed on the DISCO thesis page and being inside the
  ETH network: VPN, or the `j2tik.ethz.ch` jump host for plain ssh.

## Choosing a different model

Both ends read the model from one place, so switching is two lines and a
re-run:

```bash
# backend/.env
EMBEDDING_MODEL=intfloat/multilingual-e5-large
EMBEDDING_DIM=1024
```

Then delete `data/vectors.npy` and `data/vectors.meta.json`, because the app
refuses to mix two vector spaces rather than silently ranking nonsense, and
export, run, import again.

`multilingual-e5-large` is the strongest model `fastembed` publishes, and on a
GPU it costs no more wall-clock than the small one. The bill lands on the
laptop instead: it is 2.2 GB to load into the API process, and every query
pays roughly a second rather than 130 ms. Worth measuring with `cli eval`
before committing to it.

Models that need an instruction prefix have it applied automatically. That
table lives in two places, `MODEL_PREFIXES` here and `LOCAL_MODEL_PREFIXES`
in `core/embeddings.py`, and they have to agree, or documents and queries end
up in different corners of the same space.
