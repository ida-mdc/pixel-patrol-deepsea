#!/bin/bash
# Install everything this needs under one directory you choose, because by
# default almost none of it lands somewhere you picked.
#
#     BASE=/fast/AG_yours/$USER/pixel-patrol bash nextflow/cluster-setup.sh
#     source /fast/AG_yours/$USER/pixel-patrol/env.sh
#
# Four things otherwise go to $HOME, and a full home is a bad way to find that
# out: a JVM whose temporary files cannot be written segfaults rather than
# complaining, and an environment half-written into a full quota produces
# binaries that crash on start. Budget about 10 GB - measured, 6.5 GB of
# environment and 3.4 GB of caches, most of the first being torch and the CUDA
# libraries its PyPI wheel depends on.
#
# What this leaves behind, all under BASE:
#
#   env/       the interpreter and the packages
#   cache/     detector weights, pip's cache, torch's, everything XDG
#   nextflow/  NXF_HOME
#   env.sh     source it, then submit
#
# TORCH_INDEX picks a different torch build if you want one - the CPU-only wheels
# are at https://download.pytorch.org/whl/cpu, a ROCm or older-CUDA index likewise.
# Unset, pip does whatever it normally does.
set -euo pipefail

BASE=${BASE:?set BASE to a directory on shared storage with a few GB free}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TORCH_INDEX=${TORCH_INDEX:-}
MODEL=${MODEL:-general}

mkdir -p "$BASE"/{cache,nextflow,tmp}
BASE=$(cd "$BASE" && pwd)

echo "== installing into $BASE"

# pip unpacks every wheel into TMPDIR before installing it, and on a cluster node
# TMPDIR is /tmp: a few gigabytes, often a tmpfs sized as a fraction of RAM. torch
# and the CUDA libraries do not fit, and what that looks like is
#
#   ERROR: Could not install packages due to an OSError: [Errno 28] No space left
#
# with df reporting terabytes free on the directory being installed into, because
# the filesystem that ran out is a third one nobody was looking at.
export TMPDIR="$BASE/tmp"

export PIP_CACHE_DIR="$BASE/cache/pip"
export XDG_CACHE_HOME="$BASE/cache"

# A Python new enough to run this. Cluster pythons are often older than the
# package needs, and saying so now beats a resolver error thirty seconds in.
python_for() {
    for candidate in python3.13 python3.12 python3 python; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

if [ ! -x "$BASE/env/bin/python" ]; then
    if base_python=$(python_for); then
        echo "   python     : $("$base_python" -V) at $(command -v "$base_python")"
        "$base_python" -m venv "$BASE/env"
    elif command -v micromamba >/dev/null 2>&1; then
        echo "   python     : none on PATH is 3.12+, building one with micromamba"
        micromamba create -y -p "$BASE/env" -c conda-forge "python=3.12"
    else
        echo "no python 3.12 or newer, and no micromamba to make one." >&2
        echo "Try 'module avail python', or install micromamba into $BASE." >&2
        exit 1
    fi
fi
PY="$BASE/env/bin/python"

"$PY" -m pip -q install --upgrade pip
if [ -n "$TORCH_INDEX" ]; then
    # Asked for explicitly and installed first, so the resolver cannot decide it
    # wants a different build to satisfy the same constraint a moment later.
    echo "== torch from $TORCH_INDEX"
    "$PY" -m pip install --index-url "$TORCH_INDEX" "torch>=2.2"
fi
echo "== $REPO and its dependencies"
"$PY" -m pip install -e "$REPO"

# ffmpeg thins every recording before it is analysed, and it is a program rather
# than a library, so PyAV being installed is not the same as having one. conda has
# a real build; a venv gets the static one imageio-ffmpeg carries.
if ! "$PY" -c "from pixel_patrol_deepsea.collect import ffmpeg; ffmpeg()" 2>/dev/null; then
    echo "== ffmpeg"
    if command -v micromamba >/dev/null 2>&1 && [ -d "$BASE/env/conda-meta" ]; then
        micromamba install -y -p "$BASE/env" -c conda-forge ffmpeg
    else
        "$PY" -m pip install imageio-ffmpeg
    fi
fi
"$PY" -c "from pixel_patrol_deepsea.collect import ffmpeg; print('   ffmpeg     : ' + ffmpeg())"

echo "== the detector ($MODEL) into $XDG_CACHE_HOME"
"$PY" -m pixel_patrol_deepsea.fetch_detector --model "$MODEL"

# Nextflow, and the Java it needs, under BASE as well. A cluster module called
# `nextflow` is often a 2017-era build that fetches its own dependencies from
# Maven Central at startup over a TLS version Maven stopped accepting, which fails
# as a wall of `CAPSULE: ... handshake_failure` - and the fix is not a newer
# network, it is a newer Nextflow. Set WITH_NEXTFLOW=0 to skip all of this.
java_version() {
    "$1" -version 2>&1 | head -1 | grep -oE '"[0-9]+' | tr -d '"' || true
}

if [ "${WITH_NEXTFLOW:-1}" = 1 ]; then
    JAVA=""
    for candidate in "$BASE/jdk/bin/java" "${JAVA_HOME:-/nonexistent}/bin/java" java; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        version=$(java_version "$candidate")
        if [ -n "$version" ] && [ "$version" -ge 17 ] 2>/dev/null; then
            JAVA=$(command -v "$candidate")
            break
        fi
    done

    if [ -z "$JAVA" ]; then
        echo "== no Java 17+ anywhere; fetching one into $BASE/jdk"
        mkdir -p "$BASE/jdk"
        curl -sSL "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse" \
            | tar -xz -C "$BASE/jdk" --strip-components=1
        JAVA="$BASE/jdk/bin/java"
    fi
    export JAVA_HOME=$(cd "$(dirname "$JAVA")/.." && pwd)
    echo "   java       : $("$JAVA" -version 2>&1 | head -1)"

    if ! "$BASE/env/bin/nextflow" -version >/dev/null 2>&1; then
        echo "== nextflow into $BASE/env/bin"
        ( cd "$BASE/tmp" && curl -s https://get.nextflow.io | bash >/dev/null 2>&1 \
          && mv -f nextflow "$BASE/env/bin/nextflow" )
        chmod +x "$BASE/env/bin/nextflow"
    fi
    NXF_HOME="$BASE/nextflow" PATH="$BASE/env/bin:$PATH" \
        "$BASE/env/bin/nextflow" -version 2>&1 | grep -i version | head -2 || {
            echo "nextflow still will not start; run without it, or investigate with" >&2
            echo "  JAVA_HOME=$JAVA_HOME $BASE/env/bin/nextflow -version" >&2
        }
fi

cat > "$BASE/env.sh" <<SH
# Source this before submitting: it keeps every cache out of \$HOME and puts the
# interpreter that has the package first on PATH.
export XDG_CACHE_HOME="$BASE/cache"
export PIP_CACHE_DIR="$BASE/cache/pip"
export NXF_HOME="$BASE/nextflow"
export PATH="$BASE/env/bin:\$PATH"
export PY="$BASE/env/bin/python"
SH
if [ -x "$BASE/jdk/bin/java" ]; then
    echo "export JAVA_HOME=\"$BASE/jdk\"" >> "$BASE/env.sh"
    echo "export PATH=\"$BASE/jdk/bin:\$PATH\"" >> "$BASE/env.sh"
fi

echo
echo "== done. $(du -sh "$BASE/env" | cut -f1) of environment, $(du -sh "$BASE/cache" | cut -f1) of caches."
echo
echo "   Note: the jobs stage one recording at a time into TMPDIR - 68 MB for a NOAA"
echo "   proxy recording, 906 MB for an Axial one, twice that while it is thinned."
echo "   Check 'df -h /tmp' on a compute node; if it is small, set TMPDIR=$BASE/tmp"
echo "   for the run too, at the cost of the staging going over shared storage."
echo
echo "   source $BASE/env.sh"
echo "   OUT=$BASE/footage sbatch --partition=yours $REPO/nextflow/submit.sbatch"
