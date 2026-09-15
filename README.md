# PixelPatrol Deep-Sea (`pixel-patrol-deepsea`)

A prototype. It reads deep-sea video nobody has watched — NOAA Ocean Exploration's dive
tapes, MBARI's annotated [DeepSea-MOT](https://huggingface.co/datasets/MBARI-org/DeepSea-MOT)
sequences, the cabled camera at Axial Seamount — and answers the first question anyone has
of ten thousand hours of footage: **which minutes are worth a person's time.**

Seventeen expeditions, 274 hours, 2000 to 2026. Nothing is copied: every recording is read
from the archive that published it, and the collection page opens the archive's own file at
the second an animal was found.

It is an extension for [PixelPatrol](https://github.com/ida-mdc/pixel-patrol) — processors
that measure each slice of footage, a loader that reads an expedition manifest, and widgets
for the static viewer.

## Installing

`pyproject.toml` resolves the framework from a checkout **beside this one**:

```
repos/
  pixel-patrol/            # the framework
  pixel-patrol-deepsea/    # this repository
```

```bash
uv sync                                          # or: pip install -e .
python -m pixel_patrol_deepsea.fetch_detector    # the animal detector, ~200 MB
pytest && npm install && npm test
```

The detector is a separate step on purpose: the weights are CC-BY-4.0 but the code that
loads them (YOLOv5 v6.2) is GPL-3.0, and this package is MIT. Without it everything works
except the naming of animals.

## Using it

An expedition is an entry in `expeditions.yaml` — a listing URL, how deep to walk it, which
files to take. Adding one is the whole of "collect this too".

```bash
collect="python -m pixel_patrol_deepsea.collect"

$collect run   EX2107 collection/ --jobs 4 --dives 4 --per-dive 6   # choose, fetch, analyse, merge
$collect judge collection/parquet/                                  # re-decide what the footage was doing
$collect slim  collection/parquet/                                  # drop the pictures a report holds twice
$collect site  collection/                                          # the viewer and the collection page
$collect serve collection/                                          # look at it
```

`run` is the expensive verb and the only one that touches video; `list`, `choose`, `one`
and `merge` are its steps, for when a scheduler wants them separately.

`collect site` also writes `collection.json` beside the page - the expedition table,
90 KB - so the page can be rebuilt from that and the tile index alone. That is what
`collect page` does and what the GitHub Action in `.github/workflows/` runs: the
pictures and the reports stay on the storage, and only the 46 MB that has to be
served from the same origin as the HTML is built.
`nextflow/main.nf` runs those across a cluster: `LIST → SELECT → ANALYSE → MERGE → SITE`.

Choosing matters more than it sounds: a deep dive spends hours descending through open
water and publishes every minute of it, so `--dives 4 --per-dive 6` takes six recordings
spread across the bottom time of the four deepest dives, read out of each dive's own
report. `--jobs` is bounded by memory rather than cores — a worker running the fused
detector on HD footage sits at about 4.8 GB.

## What it measures

Per slice of footage, into one parquet per recording:

- **`slice-thumbnail`** — a small JPEG of every slice, so previews come out of the report
  rather than out of a remote recording.
- **`raster-temporal`** — frame-to-frame movement. Every other raster metric is computed
  *within* a frame, so none of them can see motion.
- **`raster-motion`** — objects moving independently of the camera, found with no model and
  no class list, so it can flag an animal no detector was trained on. Also reports
  `camera_speed`, which is a triage signal of its own: an ROV holding station is looking at
  something.
- **`raster-particles`** — small bright particles. In midwater those are largely the
  animals; near a lit seafloor they are marine snow and the measure inverts.
- **`slice-colour`** and **`slice-colour-spread`** — what colour the slice is, and how that
  colour is distributed.
- **`slice-location`** — where and when the slice was filmed, from the dive's own
  navigation. Writes the column names pixel-patrol-geospatial's map widget looks for.
- **`raster-detections`** *(needs the detector)* — how many animals are in the slice, what
  the most confident one was called, and a crop of each.

Afterwards, three passes over the finished report: **identity** links detections into
individual animals, **triage** decides what each slice of footage was doing (frozen,
subject, unnamed, dwell, empty, active), and **slim** drops the pictures a report holds
twice.

## What you get

Four widgets in the static viewer — an **Event Gallery** of ranked tiles, **Footage
Triage** with one row per recording, a **Footage Timeline** of the movement curve, and a
**Footage Barcode** that puts hours of video on one strip.

And a **collection page** over every expedition: half a sunburst of the taxonomy, a wall of
every animal found, a search box over the 373 names, and a click that opens the archive's
own recording at the second the animal was there, with the detector's box drawn over it.
Sightings can be starred, exported as CSV, and shared as a link.

## Does it work

End to end over MBARI's five annotated sequences — 4,433 detections against 5,878
annotated animals:

| | precision | recall |
| --- | --- | --- |
| everything in the report | 0.91 | 0.68 |
| at the viewer's default floor of 0.06 | 0.97 | 0.62 |
| at a floor of 0.37 | 0.99 | 0.43 |

Two things to read alongside that:

**The precision is a floor, not a figure.** The most confident detections with no
annotation under them turn out, cropped and looked at, to be real sea pens and shrimp the
benchmark does not label — it tracks a chosen set of animals rather than claiming nothing
else is in frame.

**A confident name is not a correct name.** Against five midwater clips named after the
specimen in them, the detector was right twice — emphatically right where the animal is in
its vocabulary, and wrong at 0.94 on both siphonophores and a lobate ctenophore, reaching
for `trachylinae` every time. The confidence sort ranks *what to look at*, not *what it
is*, which is why every name this package writes says it is a guess.

`collect score` re-measures all of it against whatever ground truth an expedition has.

## Where the code is

PixelPatrol has three sockets — a **processor** measures a block of pixels, a **loader**
turns a file into records, a **viewer extension** adds widgets — and this package fills all
three: 3,203 lines of Python and a 3,749-line viewer extension.

The other 6,259 lines sit outside, because there is no socket for them:

| | lines | what it is |
| --- | --- | --- |
| before pp runs | ~1,200 | which expeditions exist, which recordings to take, fetching and transcoding. PixelPatrol starts at a folder of files; this fills the folder. |
| after pp runs | ~2,400 | passes over a finished report. Each reads a parquet pp wrote and writes a better one, and pp has no notion of a pass over its own output. |
| the collection | ~2,700 | the landing page, the tile store behind it, the CLI, the static server. pp views *one* report; a collection of them is not a thing it has. |

The middle group is the one worth a socket. Triage, identity and slimming are generic and
none of them know anything about the sea.

## Where the reasoning is

Every number in this package is written down beside the thing it decided, so the code is
the documentation for why it is the way it is:

| | |
| --- | --- |
| why the detector reads each frame at three sizes | `detector.py` |
| why a crop is cut with a 60% margin | `detector.py`, `crop_of` |
| what counts as one animal seen twice | `refine.py`, `identity.py` |
| what "frozen", "dwell" and "unnamed" mean | `triage.py` |
| why nothing reads a report whole | `merge.py`, `catalogue_page.py` |
| what a report is allowed to throw away | `slim.py` |
| why the store is paged and spooled | `tiles.py` |
| how a dive's navigation is read | `locations.py` |
| what may be redistributed, and who to credit | `catalogue.py` |
| how the page works | `landing.py`, `page/landing.{html,css,js}` |

## Licence

MIT. The footage is not ours: NOAA's is public domain, MBARI's DeepSea-MOT is CC BY-SA
4.0, and the observatory's is open with a required acknowledgement. Every picture on the
collection page says which.
