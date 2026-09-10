#!/bin/sh
# The same five stages as main.nf, in plain shell, for running without Nextflow.
#
#   ./collect.sh <outdir> [expedition ...]              every listed recording
#   LIMIT=5 ./collect.sh <outdir> EX2107                5 recordings, spread
#   DIVES=3 PER_DIVE=3 ./collect.sh <outdir> EX2503     3 deepest dives, 3 each
#
# LIMIT, DIVES and PER_DIVE go to `collect choose`, which reads each dive's own
# report to find the deepest dives and the hours the vehicle was actually on the
# bottom. Without them a deep cruise spends most of its compute on the descent.
#
# Recordings whose parquet already exists are skipped, so re-running after a
# cruise gains three dives costs three recordings of work. Nothing is kept but
# parquet: each recording is staged into a scratch directory and deleted.
set -eu

OUT=${1:?usage: collect.sh <outdir> [expedition ...]}
shift
LIMIT=${LIMIT:-0}
DIVES=${DIVES:-0}
PER_DIVE=${PER_DIVE:-0}
FPS=${FPS:-10}
# The same defaults as `collect` itself: 499 classes rather than the one-class
# "fish", which calls sponges fish, and one-second slices.
DETECTOR=${DETECTOR:-general}
SLICE=${SLICE:-10}
SIZES=${SIZES:-640,960}
DETECT_EVERY=${DETECT_EVERY:-1}
ENV_NAME=${PIXEL_PATROL_ENV:-pixel-patrol}

# Run in the environment that has the package, without the caller having to know
# how it was installed. An explicit PY wins; otherwise use micromamba or conda if
# the named environment exists, and fall back to whatever python is on PATH.
if [ -z "${PY:-}" ]; then
  for manager in micromamba mamba conda; do
    if command -v "$manager" >/dev/null 2>&1 &&
       "$manager" run -n "$ENV_NAME" python -c "import pixel_patrol_deepsea" >/dev/null 2>&1; then
      PY="$manager run -n $ENV_NAME python"
      break
    fi
  done
fi
PY=${PY:-python}

# Fail now, with a sentence that says what to do, rather than on the first
# recording after the archive has been listed.
$PY -c "import pixel_patrol_deepsea" 2>/dev/null || {
  echo "cannot import pixel_patrol_deepsea with: $PY" >&2
  echo "install it, or point PY at an interpreter that has it:" >&2
  echo "  PY=\"micromamba run -n $ENV_NAME python\" $0 $OUT" >&2
  exit 1
}
$PY -c "
from pixel_patrol_deepsea import detector
import sys
if not detector.is_available():
    sys.exit('no detector set up; run: python -m pixel_patrol_deepsea.fetch_detector --model fish')
" || exit 1
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is needed to thin recordings" >&2; exit 1; }
echo "running with: $PY"

CATALOGUE=$(dirname "$0")/../src/pixel_patrol_deepsea/expeditions.yaml
EXPEDITIONS=${*:-$(sed -n 's/^- id:[[:space:]]*//p' "$CATALOGUE")}

for expedition in $EXPEDITIONS; do
  echo "== $expedition"
  mkdir -p "$OUT/manifests" "$OUT/chosen" "$OUT/parts/$expedition"
  $PY -m pixel_patrol_deepsea.collect list "$expedition" -o "$OUT/manifests/$expedition.json"

  picks=""
  [ "$DIVES" -gt 0 ] && picks="$picks --dives $DIVES"
  [ "$PER_DIVE" -gt 0 ] && picks="$picks --per-dive $PER_DIVE"
  [ "$LIMIT" -gt 0 ] && picks="$picks --most $LIMIT"
  # shellcheck disable=SC2086
  $PY -m pixel_patrol_deepsea.collect choose "$expedition" \
      -m "$OUT/manifests/$expedition.json" -o "$OUT/chosen/$expedition.json" $picks

  for url in $($PY -c "
import json,sys
print('\n'.join(json.load(open(sys.argv[1]))['videos']))" "$OUT/chosen/$expedition.json"); do
    name=$(basename "$url" .mp4)
    [ -s "$OUT/parts/$expedition/$name.parquet" ] && continue
    echo "-- $name"
    $PY -m pixel_patrol_deepsea.collect one "$url" \
        -o "$OUT/parts/$expedition/$name.parquet" -e "$expedition" \
        --fps "$FPS" --slice-frames "$SLICE" --detector "$DETECTOR" \
        --detector-sizes "$SIZES" --detect-every "$DETECT_EVERY" < /dev/null
  done

  parts=$(ls "$OUT/parts/$expedition"/*.parquet 2>/dev/null || true)
  [ -n "$parts" ] && $PY -m pixel_patrol_deepsea.collect merge "$expedition" $parts \
      -o "$OUT/parquet/$expedition.parquet"
done

$PY -m pixel_patrol_deepsea.collect site "$OUT"
echo
echo "serve it:  cd $OUT && python3 -m http.server 8090"
echo "then open: http://127.0.0.1:8090/"
