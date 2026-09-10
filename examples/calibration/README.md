# Where the detector's numbers come from

Every claim in [the package README](../../README.md#finding-as-many-as-possible-without-saying-anything-that-is-not-there)
about how much the detector finds was measured with these scripts, against MBARI's
DeepSea-MOT — the only deep-sea footage here with a box around every animal in every
frame. They are kept because a measurement nobody can repeat is an opinion, and because
the alternative was a scratch directory that gets wiped between sessions.

They are deliberately *not* the pipeline. The pipeline scores itself end to end with
`collect score`, which is the number to trust and the number on the collection page. These
run the model directly over extracted frames, which is what makes a sweep affordable:
inference is the expensive part and a confidence threshold is not, so every run keeps
every box down to a floor of 0.001 and the threshold is swept afterwards.

## Setting it up

```bash
export S=$PWD/work                     # where frames and cached detections live
mkdir -p $S

# the five annotated sequences and their ground truth, ~1.2 GB
B=https://huggingface.co/datasets/MBARI-org/DeepSea-MOT/resolve/main/data
mkdir -p ../data/dsmot && cd ../data/dsmot
for s in BD BS MWD MWS; do curl -L -o $s.mov "$B/$s/$s.mov"; curl -L -o ${s}_gt.txt "$B/$s/gt.txt"; done
curl -L -o MD_FLN.mp4 "$B/MD_FLN/MD_FLN.mp4"; curl -L -o MD_FLN_gt.txt "$B/MD_FLN/gt.txt"
cd -

# every tenth frame, as PNG, at native resolution
for s in BD BS MWD MWS MD_FLN; do
  f=../data/dsmot/$s.mov; [ -f "$f" ] || f=../data/dsmot/$s.mp4
  mkdir -p $S/frames/$s
  ffmpeg -v error -i "$f" -vf "select='not(mod(n\,10))'" -vsync 0 -start_number 0 "$S/frames/$s/%03d.png"
done
```

`GT` in `score.py` points at `../data/dsmot`; change it if the sequences live elsewhere.

## Running a sweep

```bash
# one configuration; --every 3 uses a fifth of the frames, --every 1 all of them
python infer.py full1280 --every 3 --workers 20 --agnostic
python infer.py t640x360@640 --every 6 --workers 20 --agnostic      # overlapping tiles

# fuse several cached passes, with agreement as a weight or as a gate
python fuse.py full640+agn full960+agn full1280+agn --policy mean -o fuse3_mean

# ask the following frames whether they saw it too
python corroborate.py full960+agn full960+agn_p1 full960+agn_p2 --policy mean -o t960_mean

# recall at 99%, 95% and 90% precision, on a common frame set
python score.py fuse3_mean full1280+agn --every 6 --per-sequence
```

`--every` in `score.py` matters: configurations run at different samplings are only
comparable on the frames all of them looked at, and the frame sets are nested.

## Asking what the mistakes are

This is the part that changed the conclusion. `errors.py` lists the most confident boxes
with no annotation under them and tallies them by class; `sheet.py` crops them into a
contact sheet to be looked at. On this benchmark they are real sea pens and shrimp that
the annotation does not include, so measured precision is a floor.

```bash
python errors.py fuse3_mean          # writes errors_fuse3_mean.json
python sheet.py fuse3_mean 12        # writes sheet_fuse3_mean.png
```

## The rest

- **`pooled.py`** — one precision/recall curve over every scored sequence of a *report*,
  which is what a single threshold in the viewer means. This is where the viewer's
  confidence floors come from.
- **`survey.py`** — how deep each dive of a cruise went, read from the dive's own report
  over range requests. `python survey.py EX2503 EX2306 EX2205 EX2301 EX2107`.
- **`clipstrip.py`** — one animal's clip laid out as a strip, to check that the animation
  keeps the subject the same size and in the middle. `python clipstrip.py <report> out.png`.

## What was tried, in order

| | r@p95 | verdict |
| --- | --- | --- |
| one size, 640 / 960 / 1280 / 1920 | 0.549 / 0.584 / 0.596 / 0.438 | native resolution is the *worst* |
| overlapping tiles at native scale | 0.478 | worse, and 10x the compute |
| three sizes fused, agreement weighted | **0.634** | this is what shipped |
| corroboration against the next frames | 0.641 | inside the noise, 3.4x the compute |
| the same, weighted rather than gated | 0.348 | actively harmful |
| class-aware suppression | 0.531 | the label cannot be trusted to count animals |
