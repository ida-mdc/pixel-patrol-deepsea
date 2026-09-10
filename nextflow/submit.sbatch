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

# Where main.nf is. Not simply "next to this script": sbatch copies the submit
# script into SLURM's spool directory and runs it from there, so inside a job
# $BASH_SOURCE is /var/spool/slurm/job1234/slurm_script and the workflow is not
# beside it. The submitting directory is, usually - and REPO settles it outright
# for the case where it is not.
find_workflow() {
    local candidate
    for candidate in "${REPO:-}/nextflow" "${REPO:-}" \
                     "$(dirname "${BASH_SOURCE[0]}")" \
                     "${SLURM_SUBMIT_DIR:-}/nextflow" "${SLURM_SUBMIT_DIR:-}"; do
        [ -n "$candidate" ] || continue
        if [ -f "$candidate/main.nf" ]; then
            (cd "$candidate" && pwd)
            return 0
        fi
    done
    return 1
}

if ! HERE=$(find_workflow); then
    echo "cannot find main.nf." >&2
    echo "Submit from the repository, or say where it is:" >&2
    echo "  REPO=/path/to/pixel-patrol-deepsea sbatch --partition=... \$0" >&2
    exit 1
fi
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
  echo "nextflow is not on PATH. Try 'module avail nextflow', or install one:" >&2
  echo "  micromamba install -c conda-forge -c bioconda nextflow   # brings its own Java" >&2
  exit 1
}

# Which nextflow, not just whether. A cluster module called `nextflow` is often a
# 2017-era one, and that generation fetches its own dependencies from Maven Central
# at startup over a TLS version Maven no longer accepts. What that looks like is
# forty lines of `CAPSULE: Transfer failed ... handshake_failure` and no mention of
# a version anywhere, so it is worth naming here rather than leaving to a search.
# `|| true` because that is the whole point: an old nextflow exits non-zero here,
# and under `set -e` the failure would end the script before it could say why.
version=$(nextflow -version 2>&1 | grep -oE 'version [0-9]+\.[0-9]+' | head -1 | cut -d' ' -f2 || true)
if [ -z "$version" ]; then
  echo "'nextflow -version' did not report a version. If it printed CAPSULE lines and" >&2
  echo "handshake_failure, this is a pre-2018 Nextflow that cannot reach Maven Central" >&2
  echo "over modern TLS - it is too old to run this workflow whatever the network does." >&2
  echo >&2
  echo "  module avail nextflow                                     # a newer module?" >&2
  echo "  micromamba create -n nf -c conda-forge -c bioconda nextflow  # brings its own Java" >&2
  echo "  curl -s https://get.nextflow.io | bash                    # needs Java 17+" >&2
  exit 1
fi
case "$version" in
  0.*|1[0-9].*|2[0-2].*)
    echo "nextflow $version is too old; this workflow needs 23.04 or newer." >&2
    echo "A conda one brings its own Java:" >&2
    echo "  micromamba create -n nf -c conda-forge -c bioconda nextflow" >&2
    exit 1;;
esac
echo "   nextflow       : $version"

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
# The staging directory is the opposite ask. One recording is fetched into it,
# thinned - so two copies briefly - analysed and deleted, and none of that needs
# to leave the node. 68 MB for a NOAA proxy recording, 906 MB for one of the Axial
# camera's, so a couple of gigabytes of node-local space per running task. Left to
# the scheduler's own TMPDIR where there is one, because that is the thing the
# cluster already cleans up after a job.
echo "   staging via     : ${TMPDIR:-/tmp} (node-local is right for this one)"

config=$(mktemp)
trap 'rm -f "$config"' EXIT
printf "process.queue = '%s'\n" "$PARTITION" > "$config"

choices=(--dives "$DIVES" --perDive "$PER_DIVE" --limit "$LIMIT")
[ -n "$EXPEDITIONS" ] && choices+=(--expeditions "$EXPEDITIONS")

exec nextflow run "$HERE/main.nf" \
    -profile "$PROFILE" -c "$config" \
    -w "$WORK" --outdir "$OUT" --python "$PY" \
    "${choices[@]}" -resume
