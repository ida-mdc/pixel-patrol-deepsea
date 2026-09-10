#!/bin/bash
#SBATCH --job-name=deepsea-collect
#SBATCH --output=deepsea-collect-%j.log
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=48:00:00
#
# Collect the catalogue on a SLURM cluster.
#
#     sbatch --partition=YOURS nextflow/submit.sbatch
#     OUT=/scratch/$USER/footage DIVES=5 PER_DIVE=8 sbatch --partition=YOURS nextflow/submit.sbatch
#
# This job is the *driver*: one core doing nothing but running Nextflow, which
# submits one job per recording and waits. It asks for two days because it lives
# as long as the whole collection does; the work is in the jobs it submits, each
# of which asks for its own time. Submitting the driver rather than running it on
# a login node is the difference between a collection that survives a dropped ssh
# session and one that does not - and most clusters kill long login-node processes
# anyway.
#
# Everything is resumable. Re-submitting after any interruption re-lists the
# archives, finds the recordings whose parquet is already published, and analyses
# only what is missing.
#
#   OUT        where the collection goes. Must be visible from every compute node.
#   WORK       Nextflow's work directory; same requirement. Defaults to $OUT/work.
#   PY         the interpreter that has pixel_patrol_deepsea installed.
#   DIVES      deepest dives per expedition (0 for all)
#   PER_DIVE   recordings per dive, spread over its bottom time (0 for all)
#   LIMIT      cap on recordings per expedition (0 for none)
#   EXPEDITIONS  comma-separated ids; default is the whole catalogue
#   PROFILE    `slurm` (the point of this) or `local` to try it on one machine

set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
OUT=${OUT:-$PWD/footage}
WORK=${WORK:-$OUT/work}
PY=${PY:-$(command -v python)}
DIVES=${DIVES:-5}
PER_DIVE=${PER_DIVE:-8}
LIMIT=${LIMIT:-0}
EXPEDITIONS=${EXPEDITIONS:-}
# `slurm` is the point of this script; `local` is here so the same command can be
# tried on one machine first, which is the cheapest way to find out that the
# environment is wrong before a queue tells you.
PROFILE=${PROFILE:-slurm}

# The partition the *tasks* go to. Inherited from the driver's own allocation when
# it has one, so `sbatch --partition=x` sets it for both and there is one place to
# say it. `batch` is a guess and every cluster names these differently.
PARTITION=${PARTITION:-${SLURM_JOB_PARTITION:-batch}}

echo "== collecting into $OUT"
echo "   work directory : $WORK"
echo "   interpreter    : $PY"
echo "   profile        : $PROFILE"
[ "$PROFILE" = slurm ] && echo "   partition      : $PARTITION"
echo "   choosing       : ${DIVES} dives, ${PER_DIVE} per dive, limit ${LIMIT:-0}"

command -v nextflow >/dev/null 2>&1 || {
  echo "nextflow is not on PATH. Try 'module load nextflow', or install it:" >&2
  echo "  curl -s https://get.nextflow.io | bash && mv nextflow ~/bin/" >&2
  exit 1
}

# The checks worth doing before submitting a thousand jobs rather than after.
# Each one is a failure that would otherwise show up as every task failing
# identically, twenty minutes into a queue.
"$PY" -c "import pixel_patrol_deepsea" 2>/dev/null || {
  echo "$PY cannot import pixel_patrol_deepsea. Install this checkout into it:" >&2
  echo "  $PY -m pip install -e $REPO" >&2
  exit 1
}
"$PY" -c "
import sys
from pixel_patrol_deepsea import detector
if not detector.is_available():
    sys.exit('no detector in the cache reachable from this interpreter')
" || {
  echo "Fetch it once, on a filesystem the compute nodes share:" >&2
  echo "  $PY -m pixel_patrol_deepsea.fetch_detector --model general" >&2
  exit 1
}

mkdir -p "$OUT" "$WORK"
# Nextflow stages every task through the work directory, so a node that cannot see
# it produces a run where every task fails to start. Cheaper to say so here. Only
# a problem when the tasks are on other machines: a local run has one filesystem
# and node-local scratch is the right place for it.
if [ "$PROFILE" = slurm ]; then
  case "$WORK" in
    /tmp/*|/dev/shm/*|/scratch/local/*)
      echo "WORK is $WORK, which is usually node-local storage: the compute nodes" >&2
      echo "would each see a different empty directory. Point it at shared storage." >&2
      exit 1;;
  esac
fi

config=$(mktemp)
trap 'rm -f "$config"' EXIT
printf "process.queue = '%s'\n" "$PARTITION" > "$config"

choices=(--dives "$DIVES" --perDive "$PER_DIVE" --limit "$LIMIT")
[ -n "$EXPEDITIONS" ] && choices+=(--expeditions "$EXPEDITIONS")

exec nextflow run "$HERE/main.nf" \
    -profile "$PROFILE" -c "$config" \
    -w "$WORK" --outdir "$OUT" --python "$PY" \
    "${choices[@]}" -resume
