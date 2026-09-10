# PixelPatrol Deep-Sea Package (`pixel-patrol-deepsea`)

Extension for **PixelPatrol** for reading long video recordings — footage that nobody
has watched yet, where the first question is *which minutes are worth a person's time*.

Built and validated against raw submersible dive tapes from
[NOAA Ocean Exploration](https://www.ncei.noaa.gov/data/oceans/oer/video/), MBARI's
annotated [DeepSea-MOT](https://huggingface.co/datasets/MBARI-org/DeepSea-MOT) sequences,
and the single-specimen midwater recordings published on Zenodo by Burns and Phillips
(the `RAD2-*` records), which are read straight out of their archives without
downloading them.

Those records carry a title, three creators, a licence and nothing else — no cruise, no
vessel, no date. Their data descriptor ([Burns et al., *Scientific Data* 11,
2024](https://www.nature.com/articles/s41597-024-03533-4)) places the collection in the
Eastern Pacific in August 2021, which is Schmidt Ocean's *Designing the Future 2*
(FK210812, R/V *Falkor*, ROV SuBastian) — Brennan Phillips chief scientist, David Gruber
co-PI, John Burns on the team. That chain comes from the paper and the expedition page,
not from the archives themselves. `RAD2` is the Rotary Actuated Dodecahedron sampler the
specimens were caught with.

## Installing

The package is an extension: it registers processors, a loader and a viewer plugin with
[PixelPatrol](https://github.com/ida-mdc/pixel-patrol) through entry points, and it
needs `pixel-patrol-base` and `pixel-patrol-loader-video` to do anything. `pyproject.toml`
resolves those from a checkout of the main repository **beside this one**:

```
repos/
  pixel-patrol/            # the framework, packages/pixel-patrol-base et al.
  pixel-patrol-deepsea/    # this repository
```

```bash
uv sync                                   # or: pip install -e .
python -m pixel_patrol_deepsea.fetch_detector   # the animal detector, ~200 MB
```

The Python tests need nothing else; the widget tests need the JS toolchain:

```bash
pytest
npm install && npm test
```

## Processors

- **`slice-thumbnail`** — a small JPEG of every slice, about 3 KB each. This is what makes
  the report readable: previews and animations come out of the parquet instead of being
  seeked out of a remote recording, so the gallery fills instantly and works offline.
- **`raster-temporal`** — frame-to-frame movement along `T`: the mean and peak absolute
  difference between consecutive frames, plus the pair count they average over. Every
  other raster metric is computed *within* a frame, so none of them can see motion.
- **`raster-motion`** — objects moving independently of the camera, found with no model
  and no class list, so it can flag an animal no detector was trained on. The camera's own
  motion is estimated by phase correlation and removed before anything is subtracted;
  where it is too fast to register, or the scene too three-dimensional for one shift to
  describe, that is reported rather than guessed at. Reports `camera_speed` in px/s, which
  is a triage signal in its own right — an ROV holding station is looking at something.
- **`raster-particles`** — counts small bright particles per slice, using a band-pass
  filter and a minimum area. In midwater footage those particles are largely the animals
  (0.85 precision, 0.39 recall against DeepSea-MOT). Near a lit seafloor they are marine
  snow and the measure inverts. It is a particulate-load measure, not an animal detector;
  the module docstring records both measurements.
- **`slice-location`** — where and when each slice was filmed: `latitude`, `longitude`,
  `depth_m`, `altitude_m`, `recorded_at`, `footprint`, and `location_source` saying which
  published record the fix came from. The one processor here that looks at no pixels at
  all; see [Where and when the footage was taken](#where-and-when-the-footage-was-taken).

These need per-slice granularity, so process with a `T` slice size:

```bash
pixel-patrol process dive_tapes/ -o dive.parquet --loader video --slice-size T=30
```

### Which detector to run

Three are fetchable, and the choice matters more than any tuning:

| `--model` | classes | for |
| --- | --- | --- |
| `general` | 499 | the default. FathomNet's MBARI-315k, midwater and benthic, with words for `Porifera`, `Crinoidea`, `Actinopterygii` — and for `equipment` and `geologic` |
| `fish` | 1 | megafishdetector, MIT. Precise boxes on fish, but everything it fires on is called `fish` |
| `midwater` | 22 | gelatinous zooplankton and no fish class at all |

Measured on thirty frames of benthic dive footage: `fish` returned six detections, all
labelled `fish`. `general` returned twenty — `Porifera` 0.64, `Ceriantharia` 0.74,
`Actinopterygii` 0.51, plus `Anguilliformes`, `Lycenchelys`, `Crinoidea`, `Munidopsis`,
`Polychaeta`. The sponges in that footage are sponges either way; only one of the two
says so, and a page that counts animals is only as honest as the vocabulary behind it.

### Optional: an actual animal detector

`raster-detections` runs a [FathomNet](https://fathomnet.org)-trained detector and reports
how many animals are in each slice, and what the most confident one was called. It appears
in `pixel-patrol list` only once you have fetched a model:

```bash
python -m pixel_patrol_deepsea.fetch_detector
```

That is a separate step on purpose. The weights are CC-BY-4.0, but the code that loads them
— YOLOv5 v6.2 — is **GPL-3.0**, and this package is MIT, so nothing GPL is shipped or
declared as a dependency. Tune it with `PIXEL_PATROL_DETECTOR_SIZE` (default 1280),
`PIXEL_PATROL_DETECTOR_CONFIDENCE` (0.25) and `PIXEL_PATROL_DETECTOR_EVERY` (3 seconds).

Measured against MBARI's annotated midwater sequences: 0.85 precision at 0.27 recall at
1280 px, or 0.95 precision at 0.10 recall at the default confidence. Read it as a floor on
what is present, not a census — and note it only knows the 22 categories it was trained on,
so it is a midwater model applied to midwater footage.

## Widgets

- **Event Gallery** — the detected stretches as ranked tiles across every recording, each
  animating through stills held in the parquet. Where a detector ran it opens ranked by
  animals, and can be narrowed to one species or sorted by how sure the detector was.
  Where the report can find things it opens on **what was found** - subjects and unnamed
  movers - and leaves out the bands that only describe the footage. On the eight-hour
  dive that is 244 tiles rather than 345: the other 101 are camera movement over open
  water. *Every kind* is one click away for a QC pass, where frozen tape and long holds
  are the point. Export the timecodes as CSV for an annotation tool.
- **Footage Triage** — one row per recording: length, how much of it is dead, held, empty
  or busy, and how many animals of which species. The question you have before you watch
  anything. Opens sorted by animals wherever a detector ran.
- **Footage Timeline** — the movement curve for one recording, with detected stretches
  shaded by kind — including the stretches where a detector named an animal — and a table
  you can open.
- **Footage Barcode** — one colour strip per recording, drawn to scale, so hours of video
  read at a glance and several recordings compare side by side. A ribbon underneath marks
  where the animals were; hovering reads back the timecode, the count and the species.

## Keeping the video remote

Previews come from the parquet, so the report is fully usable with no access to the footage
at all. Give it a **footage base URL** — or open the report with `?footage=<base-url>` — and
clicking an event additionally streams that stretch from wherever the recordings live,
seeked to its timecode.

That split matters. An earlier version grabbed preview frames by seeking the recording, which
cost 43 MB of range requests to fill one screen and left tiles blank for seconds. Stored
stills cost about 3 KB each and appear immediately.

Two things this depends on. The viewer sets `Cross-Origin-Embedder-Policy: require-corp`
for DuckDB-WASM, so cross-origin video needs `crossorigin="anonymous"` (the widgets set it)
and a host that answers CORS or sends `Cross-Origin-Resource-Policy`. Seeking needs the
host to answer HTTP range requests with `206`. NOAA's archive does both.

## What it can name, and what it cannot

The detector knows twenty-two classes, all midwater:

> bathochordaeus (and its inner and outer filter), beroe, calycophorae nectosome,
> cephalopoda, cydippida, leptothecata, lobata, naked pteropod, oikopleura inner
> filter, paddle worm, physonectae nectosome, poeobius, prayidae nectosome,
> pyrosoma, scyphozoa, shelled pteropod, shrimp, solitary salp, thalassocalyce,
> trachylinae

**There is no fish class.** Gelatinous zooplankton, a shrimp, a squid and a couple of
worms is the whole vocabulary, so a fish drifting through the frame is either missed
or called something it is not. And on footage of the seafloor the names are wrong
outright: on a NOAA dive tape, gold coral, the ROV's laser dots and its sample
carousel all came back as `scyphozoa` above 0.9 confidence. Detections outside open
water are a prompt to look, never a label to keep.

## Finding the animals twice over

Inference is the expensive part of the pipeline, and an animal stays in frame far longer
than one slice — a median of 182 frames in midwater and 290 on the bottom, measured
against MBARI's annotated sequences. Sampling one frame a second already sees 96–99% of
the distinct animals a full read would. So detection is two passes:

1. **Coarse**, inside the pipeline. `PIXEL_PATROL_DETECTOR_EVERY` skips all but every Nth
   second, and only the first colour channel is looked at — the pipeline hands each
   channel to its own leaf block, so running all three costs triple for three greyscale
   copies of one moment.
2. **Fine**, `refine.py`. Takes the slices that came back with something, merges hits of
   the same species within 4 s, pads ±2 s, and walks just those stretches at 4 fps. Every
   detection gets a crop and a row, in colour.

The coarse pass therefore shows the detector one channel replicated three times, which is
not what it was trained on. Measured both ways on the same frames, it is a wash rather
than a loss — 52 detections against 29 on one clip, 13 against 17 on another, with best
confidence 0.02–0.05 lower in grey. Midwater footage is nearly monochrome to begin with.
What it does cost is the *look* of the crop, and that is what `merge_crops_into_report`
swaps back after the fine pass.

`--refine` writes `sightings.parquet` and `sightings.csv` — **one row per animal**, not
per slice — with taxon, confidence, box, frame and timecode, keyed by `name` and `dim_t`
so it joins straight onto the report. The report itself stays one row per slice: it is an
aggregation tree over image dimensions, and an animal is not one of those axes.

## A free ground truth, and what it says

The `RAD2-*` records are named after the animal in them — `RAD2-005 Atolla sp. video and
image data` — which makes each one a labelled test the detector never saw. Over the five
clips fetched so far, 748 refined sightings:

| record | detector's best call | the record's own answer | |
| --- | --- | --- | --- |
| Atolla | `scyphozoa` 0.94 ×88 | same | ✅ |
| Bathochordaeus | `bathochordaeus inner filter` 0.89 ×70 | plus `outer filter` 0.83 ×65 and `bathochordaeus` 0.77 ×92 | ✅ |
| Halistemma | `trachylinae` 0.94 ×23 | `physonectae nectosome` 0.30 ×1 | ❌ |
| Praya | `pyrosoma` 0.89 ×50 | `prayidae`/`calycophorae nectosome` — absent | ❌ |
| Lampocteis | `trachylinae` 0.96 ×60 | `lobata` — absent (`cydippida` 0.57 ×13 is at least a ctenophore) | ❌ |

**Two of five at the top.** The pattern is not random: it is right, and emphatically so,
where the animal is squarely in its vocabulary — a scyphozoan jellyfish, and a giant
larvacean whose inner and outer mucus filters it separates as distinct classes. It is
wrong, at 0.94 and higher, on both siphonophores and on the lobate ctenophore, where it
reaches for `trachylinae` every time.

So a confident name is not a correct name, and the confidence sort ranks *what to look at*
rather than *what it is*. Run the comparison yourself against whatever the fetch produced:

```bash
python deepsea_report.py --view --no-view --check-names
```

The other consequence is upstream: a clip has to be cut where the named specimen is rather
than where the detector is loudest — an Atolla record cut at the frame a passing shrimp
scored 0.9 on is not a clip of Atolla. The probes prefer a moment showing the species the
record is named after, and fall back to the most confident moment only when they never
see it.

## Finding as many as possible without saying anything that is not there

### What the pipeline actually does

End to end, `collect run DSMOT` over the five annotated sequences the catalogue held at
the time, at every slice — 288 frames, 4,433 detections against 5,878 annotated animals:

| | detections | annotated | precision | recall |
| --- | --- | --- | --- | --- |
| `BD` benthic | 2,426 | 2,870 | **0.94** | **0.79** |
| `BS` benthic | 855 | 1,388 | 0.84 | 0.52 |
| `MWD` midwater | 867 | 1,284 | 0.93 | 0.63 |
| `MWS` midwater | 225 | 194 | 0.80 | **0.92** |
| `MD_FLN` midwater | 60 | 142 | 0.68 | 0.29 |
| all together | 4,433 | 5,878 | **0.91** | **0.68** |

`BD` was 0.91 precision and 0.69 recall before this; it is now more precise *and*
finds ten points more of the animals. `BS` moved from 0.51 to 0.52 and will not move
further — a third of its annotations are never proposed at any confidence, which is
the model rather than a threshold. The three others had never been scored at all.

`MD_BTL` is a sixth sequence now and not in these numbers. All eleven sequences MBARI
publishes carry ground truth — four of them in the `gt/` directory the MOT format
specifies rather than beside the recording, which is the only reason this one looked for
a while like it had none. It is 10 MB with 374 boxes; the five that are still left out are
4K and 2.2 GB each, and two of those five are the same footage under two names.

`MD_FLN` is the one that does badly, and it fails for the same reason full resolution
does. Its animals fill the frame — the largest annotated box is 1164×1080 — and a model
that wants them at two thirds of HD has no size at which that arrives correctly.
Reading the frame at *smaller* sizes is what would help there, which is the opposite
of the fix for everything else and is why it is a knob (`--detector-sizes`) rather than
a constant.

Everything above is what is *in the report*. What a reader sees depends on where they
put the floor, and the pooled curve is the honest way to state that, because a
threshold does not know which recording a row came from:

| floor | precision | recall |
| --- | --- | --- |
| any | 0.905 | 0.683 |
| 0.04 | 0.950 | 0.649 |
| **0.06** (the viewer's default) | **0.967** | **0.621** |
| 0.37 | 0.990 | 0.425 |

Read the precision column as a floor, not a figure — see
[below](#the-precision-is-a-lower-bound-and-the-pictures-say-so).

### How that was arrived at

The sweep behind this is in [`examples/calibration/`](examples/calibration/), scripts and
all, because a measurement nobody can repeat is an opinion.

All five annotated DeepSea-MOT sequences, 300 native-resolution frames, every detection
kept down to a floor of 0.001 so the threshold could be swept afterwards rather than
guessed. Recall is measured at three precisions, because "without false positives" is not
one number - the report keeps everything and the viewer has a threshold a reader can move,
so the useful statement is how much can be found while staying this clean.

| how the frame is read | r@p99 | r@p95 | r@p90 | most it ever finds | cost |
| --- | --- | --- | --- | --- | --- |
| 640 px | 0.409 | 0.549 | 0.569 | 0.749 | 1x |
| 960 px | 0.350 | 0.584 | 0.671 | 0.835 | 2x |
| 1280 px | **0.464** | 0.596 | 0.653 | 0.859 | 3.6x |
| 1920 px, i.e. native | 0.240 | 0.438 | 0.531 | 0.842 | 4.6x |
| 640 + 960 + 1280, fused | 0.424 | **0.634** | **0.700** | 0.855 | 6.7x |
| overlapping tiles at native scale | 0.041 | 0.478 | 0.616 | 0.838 | 10x |

Four things came out of this, and two of them were surprises.

**Full resolution is the worst way to read the frame.** Not the slowest-and-best - the
worst, by a wide margin. The model has a scale it expects animals to arrive at, roughly
two thirds of HD, and feeding it more pixels than that pushes every animal past the size
its anchors were trained for.

**So tiling does not work, which is the obvious thing to try for small animals.** Cut the
frame into overlapping tiles, detect at native scale, merge with suppression across the
seams: 0.478 against 0.634, for three times the compute of the thing that wins, and it
collapses to 0.041 at 99% precision. It helped on the two midwater sequences, where the
animals are small and far apart, and wrecked both benthic ones, where a seam cuts animals
in half and a carpet of sea pens fills a tile edge to edge. Merging by containment as well
as overlap removes the halves; it cannot put back what the tile never saw whole.

**Fusing sizes is the version of that idea that works**, because it changes the scale the
animal arrives at without cutting anything up. A size that did not see an animal counts as
a vote of zero and the confidence becomes the mean, so agreement between sizes lifts a box
and a lone sighting sinks - which is the precision half. The union of what the sizes found
is the recall half. Every sequence improved, midwater most: 0.498 to 0.604.

**Ignoring the label while suppressing is worth more than it sounds.** This checkpoint
knows 499 classes and cannot tell many of them apart, so class-aware suppression returns
the same animal five times under five names and counts each as a separate find. Ignoring
the label took r@p95 from 0.531 to 0.596 and improved all five sequences. YOLOv5's default
cap of 300 boxes had to go too - an annotated seabed holds around fifty animals a frame.

### What did not work

**Corroboration between consecutive frames.** A real animal is still there a thirtieth of
a second later and a speck of noise is not, so requiring a second look to agree ought to
be free precision, and it is also what the two-pass design already pays for. Measured
against the following two frames of every scored frame it is worth +0.007 at 95% precision,
inside the noise, for 3.4x the inference. Weighting by persistence instead of filtering on
it is actively harmful - 0.634 to 0.348 - and that is the informative result: the confident
mistakes here are *persistent*. Which leads to the last finding.

### The precision is a lower bound, and the pictures say so

Cropping the most confident boxes with no annotation under them and looking at them:

- eight of the top twelve are one long thin animal on the seabed of `BD`, called
  `Funiculina-Balticina complex` at 0.32 to 0.59, its box drifting frame by frame as the
  camera moves over it. Pulled back, the seabed around it is *covered* in sea pens. The
  bright ones are annotated. This one is dimmer, and is not.
- three more are one bright shrimp - `Eusergestes similis`, 0.44 to 0.60 - crossing three
  frames of `MWD`, also unannotated.

So the boxes being counted as mistakes at the top of the ranking are real animals of
exactly the annotated kind. DeepSea-MOT is a tracking benchmark and annotates 94 tracks in
`BD`; it is not a claim that nothing else is in frame. Every precision here should be read
as a floor, and 99% precision is not reachable against this file no matter what the
detector does.

### What a published proxy costs

NOAA publishes its ROV video as 640x360 proxies - it is the only version there is. Shrinking
the annotated sequences to that size and re-measuring says what that costs and how to read
them:

| | r@p95 | most it ever finds |
| --- | --- | --- |
| native, one size at 1280 | 0.596 | 0.859 |
| native, three sizes fused | 0.634 | 0.855 |
| proxy, one size at 640 | 0.581 | 0.782 |
| proxy, three sizes fused | 0.595 | 0.803 |
| proxy, upscaled to 1280 | 0.561 | 0.806 |

The proxy costs about six points of reachable recall, and **upscaling it is worse than not
bothering** - there is no detail in it for a larger input to find. So the cruises are read
at two sizes rather than three (`--detector-sizes 640,960`), which is the second pass for
what the third was not worth.

## Collecting an archive

One dive is a demo. NOAA Ocean Exploration publishes on the order of ten thousand hours
across 119 cruises and nobody has watched most of it, so the collection is data rather
than a shell script:

```yaml
# src/pixel_patrol_deepsea/expeditions.yaml
- id: EX2107
  name: Windows to the Deep 2021
  listing: https://www.ncei.noaa.gov/data/oceans/oer/video/EX2107/Video/
  depth: 2                     # levels between the listing and the recordings
  pattern: "*ROVHD_Low.mp4"
```

Adding an entry there is the whole of "collect this too". Six verbs do the work, each
one thing so a scheduler can redo only what changed:

```bash
python -m pixel_patrol_deepsea.collect list   EX2107 -o manifests/EX2107.json
python -m pixel_patrol_deepsea.collect choose EX2107 -m manifests/EX2107.json -o chosen/EX2107.json \
    --dives 3 --per-dive 3
python -m pixel_patrol_deepsea.collect one    <url>  -o parts/EX2107/<name>.parquet -e EX2107
python -m pixel_patrol_deepsea.collect merge  EX2107 parts/EX2107/*.parquet -o parquet/EX2107.parquet
python -m pixel_patrol_deepsea.collect score  EX2107 collection/
python -m pixel_patrol_deepsea.collect site   collection/
```

`run` is all of that for one expedition on one machine, and it differs from `one` in a
loop in the two ways that decide whether a night was well spent — it **chooses** the
recordings, and it analyses several at once:

```bash
python -m pixel_patrol_deepsea.collect run EX2503 collection/ \
    --jobs 10 --dives 4 --per-dive 6 --detector-sizes 640,960
```

Choosing matters more than it sounds. EX2503 publishes 127 five-minute recordings for a
single dive — ten and a half hours for one of sixteen dives — and a deep dive spends hours
descending through open water and publishes every minute of it. Each dive's own report
states when the vehicle reached the bottom and when it left, and what its maximum depth
was, so `--dives 4` takes the four deepest and `--per-dive 6` takes six recordings spread
across their bottom time. Reading that costs one ranged read of a text file per dive
(`locations.noaa_dive_summary`), not a download of the dive.

`choose` is the same decision as a step of its own, for when the scheduler is not this
process — it writes the subset as a manifest of its own and leaves the listing alone,
because the catalogue page counts what an expedition published, not what we picked out of
it. **The archive writes those reports in two notations**: cruises from 2021 on say
`Max Vehicle Depth` in decimal degrees, and 2016 to 2019 say `Max. depth` in
degrees-and-minutes. Reading only the newer one does not give a cruise without depths, it
gives a cruise whose descent is analysed — which is what happened to EX1903L2 and EX1605L1
until both notations were read. EX1605L1 turned out to hold the deepest dive in the
catalogue, 4996 m, which nothing here knew while its reports were unreadable.

`--jobs` is bounded by memory, not cores: a worker running the fused detector on HD footage
is resident at about 4.8 GB, so what fits at once is roughly RAM over five gigabytes.

`examples/overnight.py` is a plan across all of it — the whole catalogue, resumable, ground
truth first — and is the thing to read for how the settings differ per archive.

`nextflow/main.nf` runs them: `LIST → SELECT → ANALYSE → MERGE → SITE`, one parquet per
recording merged into one per expedition. `SELECT` is `choose`, and it is there because
without it a workflow has exactly one cheap way to sample a cruise — the first N
recordings — and on a deep dive those are the vehicle descending. New work is found two
ways, because they catch different things — `-resume` skips any `ANALYSE` whose inputs are
unchanged, and a recording whose parquet is already published is skipped outright, so a
manifest that grew by three dives means three tasks even on a fresh work directory.

```bash
nextflow run nextflow/main.nf --outdir /data/footage -profile local -resume
nextflow run nextflow/main.nf --outdir /data/footage --expeditions EX2107 --limit 20
nextflow run nextflow/main.nf --outdir /data/footage -profile slurm --dives 3 --perDive 3
```

On a cluster, four things are worth knowing before submitting. `--dives`/`--perDive`/
`--limit` default to *everything*, which for this catalogue is about eleven thousand
recordings and some nine hundred hours. The `slurm` profile asks for a two-hour walltime
per task, because a queue whose default is shorter than a recording is how a long
collection dies at 40%. The detector is a 200 MB cache under `$HOME/.cache/pixel-patrol`,
and the workflow checks it is reachable before submitting anything rather than letting a
thousand tasks discover it one at a time. And `cleanup` is off: a recording is deleted by
its own task either way, so cleaning the work tree saves nothing and costs `-resume` the
cache it exists for.

Run against Nextflow 26.04.6 as well as 25.10, which needed two things the older parser
accepted: a top-level helper has to be a function rather than a closure assigned to a
name, and a `publishDir` whose path depends on an input value has to be a closure rather
than a string, since 26 resolves the string when the process is defined and nothing is
bound to it yet.

Without Nextflow installed, `nextflow/collect.sh` runs the same five stages in plain
shell with the same skip-what-is-done behaviour:

```bash
LIMIT=5 nextflow/collect.sh collection/ EX2107          # five recordings, spread
DIVES=3 PER_DIVE=3 nextflow/collect.sh collection/      # three dives each, on the bottom
nextflow/collect.sh collection/                         # the whole catalogue
```

Merging is a concatenation, not a re-aggregation: in pixel-patrol a video file is one
image, so every level of the tree in a part already belongs to that recording alone.
Two recordings merged give two `obs_level` 0 rows, which is what a single run over both
would have produced.

### What "no download" does and does not mean

`one` is the only verb that touches video, and it keeps none: the recording is staged
into the task's own directory, analysed, and deleted with it. A collection of any size
costs one recording of transient disk — the two-recording test above left 780 KB behind,
all of it parquet.

Going further, and handing the pipeline a URL instead of a path, needs a change in
`pixel-patrol-base` rather than here: file discovery is filesystem-bound throughout —
`os.walk` for the tree, `os.stat` for size and modification date, `commonpath` for the
shared root. That wants the source abstraction the S3 work is adding. Nothing is
retained either way; the difference is one recording of scratch.

## The collection page

```bash
python -m pixel_patrol_deepsea.collect site collection/
```

Writes **one** `index.html` beside a static viewer — the only page this package
produces. It carries the headline numbers, a tile per species with the best crop of it,
an animated tile per animal orderable by confidence, time, species or duration, a card
per expedition with progress, provenance and species, and a link to that expedition's
report. The viewer is **rebuilt every time**, not
skipped when one is already there: the site carries its own copy of every widget, so a
viewer left from an earlier run serves the widgets as they were then, and does it
silently. `pixel-patrol view` needs no such step — it reads the plugin out of the
installed package on each request, so a widget edit is live on reload.

one row per expedition with how many of its
recordings are listed and how many are processed, hours analysed, animals or slices with
animals, species count, and a link to that expedition's report. The link is a `?data=`
URL into the viewer next to it, so opening a report needs a static file server and
nothing else — no Python, no port, no viewer process. The footage widgets are bundled
into that viewer automatically, so the gallery and the motion bands are there.

## Where and when the footage was taken

Footage without a position is footage you cannot compare with anything, and two dives in
the same canyon ten years apart are the interesting question in a collection like this.
None of it is in the video: these recordings carry no GPS track, no telemetry channel, and
— checked — not even a burnt-in overlay to read. What the archives publish *beside* the
video is enough.

**The clock is the filename.** `EX2107_VID_20211027T124027Z_ROVHD_Low.mp4` and
`CAMHDA301-20160815T000000Z.mov` both state the UTC second the recording started, so a
slice `n` seconds in has a real time. Preferred over the container's `creation_time` even
where that exists: the filename is what the archive indexes and what its dive logs join
against, and it survives a transcode.

**The position is one of three things**, and `location_source` always says which, because
they are not the same claim:

| | what it fixes | where it comes from |
| --- | --- | --- |
| a 1 Hz vehicle track | every slice, with depth | `RovTrack1Hz.csv`, inside the dive's ancillary-data zip |
| a dive path | the dive, no depth | `*_Path.kml` — a bare line of coordinates with no times on it |
| a deployment register | a camera that does not move | the observatory's own asset register |

The first is the good one and it is real per-second navigation: on EX2107 dive 1 the depth
climbs 172 → 201 → 230 → 866 m over the first hour, and `altitude_m` drops to a metre or
two once the vehicle is flying the bottom — which is a decent proxy for whether a slice
shows the seabed or open water. It is read out of a 16 MB zip over range requests, for the
one member wanted, in about five seconds.

**Both of these come in two notations, and reading only the newer one is silent.** The
2016 cruises head their track `time (unix sec), lat (dec. deg.), ... depth (m)` and put
depth after longitude; the 2019-and-later ones head it `UNIXTIME,DEPTH,ALT,LAT_DD,LON_DD`.
Their dive reports differ the same way — `Max. depth` and `28°, 15.148' N` against
`Max Vehicle Depth` and `28.2525`. A parser that knows one of the two does not announce
that it is looking at the other; it returns a cruise with no depth, no bottom time and no
position, and the pipeline goes on to analyse its descent. So the columns are matched by
name rather than by position, both notations of a coordinate are read, and a moment whose
position is `N/A` keeps its time rather than being dropped whole.

The recipe lives in the catalogue rather than in code, because the answer differs per dive
and per deployment and the archive is the one that knows it:

```yaml
  location:
    kind: noaa-dive
    data: https://oer.hpc.msstate.edu/okeanos/ex2107/
```

```yaml
  location:                    # a camera bolted to the seafloor since 2015
    kind: ooi-deployment
    reference: RS03ASHS-PN03B-06-CAMHDA301
```

That last one is looked up per recording rather than written down, because the instrument
is recovered and reinstalled: the 2016 recording resolves to deployment 3 at 1543 m and the
2026 one to deployment 12 a few metres away.

The processor that puts this on every slice is `slice-location`, and it is the one
processor here that looks at no pixels at all. It cannot do the lookup itself — a processor
is handed a block of pixels and the dimensions it sits at, not the name of the file it came
from — so `collect one`, which handles exactly one recording and does know its URL,
resolves it once and leaves it in the environment.

The column names are not free choices. `latitude`, `longitude` and `footprint` are what
pixel-patrol-geospatial's map widget queries for, so writing those names means the map
appears in the viewer with nothing further to do, with the dive's own navigation drawn as
its track. It wants all three, which is why a fixed camera gets a `Point` footprint rather
than none.

## Getting the footage

`remote_archive.py` reads video out of a huge remote zip without downloading it. The
specimen records on Zenodo are 7–22 GB each and hold a single 4K recording;
a zip keeps its index at the end and these store the video uncompressed, so the wanted
seconds can be addressed directly and written into a sparse local file. Twenty seconds of
a 7.5 GB archive costs a few hundred megabytes rather than all of it.

Zenodo throttles this, and the shape of the refusal is worth knowing: the record
metadata and the zip index come back fine while the byte ranges answer `504 Gateway
Time-out`, for hours at a time and only on some records. It is load, not a block. The
backoff retries five times per request and then gives up on that record rather than the
run; the answer to a record that keeps failing is to come back later.

### Reading a recording without keeping it

`remote_file.py` hands a decoder a file that lives on a web server:

```python
import av
from pixel_patrol_deepsea.remote_file import remote_video
with av.open(remote_video(url)) as container: ...
```

Nothing is written to disk and only the ranges the decoder asks for are fetched.
Measured on one 69 MB NOAA segment:

| what was decoded | frames | bytes fetched |
| --- | --- | --- |
| the first 60 frames | 60 | 1.1 MB (1.5%) |
| 3 s out of every 30 s | 910 | 21.8 MB (32%) |
| 3 s out of every 10 s | 2,730 | 63.7 MB (92%) |
| every frame | 8,989 | 69.0 MB (100%) |

Two things follow. Skipping about is genuinely cheaper than reading through — a tenth
of the footage for a third of the bytes — but it is not a tenth of the bytes, because
a frame cannot be decoded without the ones it was predicted from, so whole groups
come down whichever single frame you wanted. And decoding *fewer* frames does not
help by itself: asking ffmpeg for keyframes only still reads the entire file, because
it skips the decoding, not the reading.

So for a slice of a large archive, read it remotely. For a whole recording you intend
to analyse end to end, a transcoded local proxy is cheaper than the network: NOAA's
8.2-hour dive is 6.8 GB as published and 370 MB at 640×360 and 10 fps, which is less
than a *sixth* of what streaming a third of it would cost.

`examples/deepsea_report.py` puts it together — fetch, transcode, process, refine, index:

```bash
python deepsea_report.py --refine        # midwater sequences, crops and sightings
python deepsea_report.py --with-specimens # a clip of each named midwater specimen
python deepsea_report.py --with-noaa     # a full-length benthic dive tape
python deepsea_report.py --check-names   # score the detector against the record names
python deepsea_report.py --index         # rewrite the page listing every report
python deepsea_report.py --view          # just open what is already built

# everything, unattended:
python deepsea_report.py --with-specimens --refine --index --no-view
```

## A cost worth knowing

Per 30-frame HD slice, one channel: `raster-quality` **4.41 s** — of which `spectral_slope`
is 3.77 s — against a single-size detector pass's **1.53 s**. Quality metrics cannot screen
for the detector; the screen costs more than what it saves. `raster-quality` also runs on
every frame of every slice, where three would give the same answer six times faster. The
example excludes it, and the widgets fall back from `laplacian_variance` to `std_intensity`
for the empty/dwell split.

Reading a frame at three sizes and fusing them is **14.6 s** of CPU on an HD frame —
2.2 s at 640 px, 4.4 s at 960, 8.0 s at 1280 — which is now most of the pipeline and the
reason `--detect-every` exists. Two numbers around it are worth knowing because both were
mistakes here:

- a worker running it is resident at **4.8 GB**, so `--mb-per-task` must be 8192 and
  parallelism is RAM over five gigabytes, not the core count;
- the clip tracker greyscales the frame it is comparing, and doing that with a numpy mean
  over the channel axis cost **75 ms** a frame against OpenCV's **0.4 ms**. Called for the
  frame either side of every step of every animal's clip, that was 8 s a slice — half the
  runtime — to save 0.05 s. It is now one conversion per frame of the slice, through
  `cv2.cvtColor`.

See the main [pixel-patrol documentation](https://github.com/ida-mdc/pixel-patrol/) for usage.
