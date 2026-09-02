#!/bin/bash
# Embed an exported chunk file on the TIK cluster.
#
#   sbatch job.sh pending.jsonl vectors.npz
#
# One GPU is enough and the job is short. See cluster/README.md for the
# conda environment this expects and for the CPU-only variant.

#SBATCH --mail-type=NONE
#SBATCH --output=/itet-stor/abayat/net_scratch/internship-finder/jobs/%j.out
#SBATCH --error=/itet-stor/abayat/net_scratch/internship-finder/jobs/%j.err
#SBATCH --job-name=embed-chunks
#SBATCH --mem=24G
#SBATCH --nodes=1
#SBATCH --cpus-per-task=8
#SBATCH --time=02:00:00
#SBATCH --gres=gpu:1
# The high-memory nodes are reserved for the high-mem group; a 500 MB embedding
# model has no business on an A100.
#SBATCH --exclude=tikgpu[06-10]

set -o errexit

ETH_USERNAME=abayat
PROJECT=internship-finder
DIRECTORY=/itet-stor/${ETH_USERNAME}/net_scratch/${PROJECT}
CONDA_ENVIRONMENT=embed
# net_scratch, not a temp dir: the model is ~500 MB and re-downloading it on
# every job is rude to the Hub and slow.
MODEL_CACHE=/itet-stor/${ETH_USERNAME}/net_scratch/fastembed_cache

EXPORT_FILE=${1:?usage: sbatch job.sh <export.jsonl> <out.npz>}
OUT_FILE=${2:?usage: sbatch job.sh <export.jsonl> <out.npz>}

mkdir -p "${DIRECTORY}/jobs"

# A job-local temp directory that cleans up after itself, per the cluster guide.
TMPDIR=$(mktemp -d)
[[ -d ${TMPDIR} ]] || { echo 'failed to create temp directory' >&2; exit 1; }
trap "exit 1" HUP INT TERM
trap 'rm -rf "${TMPDIR}"' EXIT
export TMPDIR

echo "Running on node: $(hostname)"
echo "Starting on:     $(date)"
echo "SLURM_JOB_ID:    ${SLURM_JOB_ID}"

[[ -f /itet-stor/${ETH_USERNAME}/net_scratch/conda/bin/conda ]] &&
    eval "$(/itet-stor/${ETH_USERNAME}/net_scratch/conda/bin/conda shell.bash hook)"
conda activate ${CONDA_ENVIRONMENT}
echo "Conda activated: $(python --version)"

cd "${DIRECTORY}"

# onnxruntime-gpu links against CUDA libraries it does not ship -- cuBLAS,
# cuDNN, cuFFT and friends. The nvidia-*-cu12 pip wheels provide them, but
# nothing puts them on the loader's path, so a GPU job otherwise dies on
# "libcublasLt.so.12: cannot open shared object file" and falls back to the
# CPU. This collects every lib directory those wheels installed.
CUDA_LIBS=$(python - <<'PYEOF'
import os
try:
    import nvidia
except ImportError:
    raise SystemExit
root = os.path.dirname(nvidia.__file__)
paths = [
    os.path.join(root, name, "lib")
    for name in sorted(os.listdir(root))
    if os.path.isdir(os.path.join(root, name, "lib"))
]
print(":".join(paths))
PYEOF
)
if [[ -n ${CUDA_LIBS} ]]; then
    export LD_LIBRARY_PATH=${CUDA_LIBS}:${LD_LIBRARY_PATH}
    echo "CUDA libraries: ${CUDA_LIBS}"
else
    echo "no nvidia-*-cu12 wheels found; the CUDA provider will not load" >&2
fi

# --cuda makes a missing GPU an error rather than an hour of silent CPU work.
# --threads matches --cpus-per-task above; left to itself ONNX Runtime happily
# spawns a thread per core on the node, including the ones this job was not given.
python embed_chunks.py "${EXPORT_FILE}" \
    --out "${OUT_FILE}" \
    --cuda \
    --batch-size 512 \
    --threads "${SLURM_CPUS_PER_TASK:-8}" \
    --cache-dir "${MODEL_CACHE}"

echo "Finished at: $(date)"
exit 0
