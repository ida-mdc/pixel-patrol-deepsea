# Developing

Notes for running, extending and reading this. What it is and what it produces is in
[the README](../README.md).

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
files to take. That entry is all that "collect this too" requires.

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

The middle group is the one worth a socket. The triage pass, the identity pass and the
slimming are generic and
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
