"""The page a collection opens on.

Not a document about the collection - a way into it. The catalogue page it
replaces was a list: a wall of thirty species tiles built into the HTML, a card per
expedition, and a link. That is the right shape for a report of one dive and the
wrong one for tens of thousands of animals, where the question is not "what is the
best sponge" but "show me every sponge".

It reads as a dive log rather than as a product, because that is what it is: a
cruise report has a header stamp, figures in a monospace column, hairlines between
them and numbered sections, and it earns all of that by being a record of work
rather than an argument for it. The pictures are a contact sheet - square, edge to
edge, the frames doing the talking. Cyan is the only accent, and the footage chose
it: every metre of depth takes more red out, so a deep-sea frame is cyan whatever
is in it. Amber is kept for the one thing that has to interrupt somebody.

Four numbered parts, and a warning that is not one of them:

    01 the collection   what this is and where the footage comes from, in three
                        sentences, over the figures that say how much of it there
                        is. Short: the pictures are the point of the page.
       the disclaimer   what the figures are worth, in one line that cannot be
                        missed. The detail is behind a summary for whoever wants it.
    02 what is in it    a half sunburst against the left edge and, beside it, every
                        picture of whatever branch is in focus. Grouped by phylum,
                        which is the rank the reports group by. The pictures scroll
                        in their own box: a wall that grew the page put everything
                        below it out of reach.
    03 the expeditions  one line each: how much was analysed, and the way in.
    04 whose work this is

Nothing below the header is built into the HTML - it is read from `tiles/index.json`
and the paged files beside it, which is why the page is the same size whether the
collection holds one expedition or fifty.

What the page will not do is describe an animal in its own words. It says what the
detector called it, how sure it was, how long it stayed in view, where the register
puts the name and - where there is an article to link - what the encyclopaedia calls
it. A paragraph of natural history written to fill the box would be the one thing on
the page with no source behind it.
"""

import html
import json
from datetime import datetime
from typing import Dict, Optional

ASSET = "assets/pixel-patrol-deepsea.png"


def render(rows, index: Optional[Dict] = None, scores: Optional[Dict] = None) -> str:
    from pixel_patrol_deepsea.catalogue_page import _clock, _report_url
    from pixel_patrol_deepsea.collect import EVERYTHING

    listed = sum(r.listed for r in rows)
    processed = sum(r.processed for r in rows)
    seconds = sum(r.seconds for r in rows)
    animals = sum(r.animals for r in rows)
    # Animals are counted in a second pass. Until it has run there are only slices
    # the detector fired on, and one animal can occupy twenty of them - so say which
    # of the two a number is rather than reporting no animals at all.
    seen = sum(r.with_animals for r in rows)
    taxa = len((index or {}).get("taxa", {}))
    everything = [r for r in rows if r.report is not None]
    # The catalogue lists more expeditions than have been read; the stamp is about
    # what was read, and the table below accounts for the rest.
    ran = len([r for r in rows if r.processed])
    # Grouped by expedition, because the only reason to open every expedition in
    # one report is to see how they differ from each other.
    combined = (_report_url(f"../parquet/{EVERYTHING}.parquet", group="expedition")
                if len(everything) > 1 else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deep-sea footage collection</title>
<style>{STYLE}</style></head>
<body>
<header class="bar">
  <span class="who"><b>Pixel Patrol</b> · deep sea</span>
  <span class="stamp">{ran} expeditions · read {datetime.now().strftime('%Y-%m-%d')}</span>
</header>
<main>
  <section class="hero">
    <div class="hero-art">
      <img src="{ASSET}" alt="A diver with a tablet and a torch, an anglerfish, and a
           hydrothermal vent" loading="eager">
    </div>
    <div class="hero-say">
      <p class="chapter">01 / the collection</p>
      <h1>Somebody has to<br>watch the tapes.</h1>
      <p class="lede">NOAA, MBARI and the cabled observatories publish their dive
         footage in full - more hours of it than anybody is going to sit through.
         This reads it a frame a second and asks two questions: what moved, and
         what was it.</p>
      {_together(combined)}
    </div>
  </section>

  <div class="readout">
    {_cell(_clock(seconds), "footage read")}
    {_cell(f"{processed:,} / {listed:,}" if listed else f"{processed:,}", "recordings")}
    {_cell(f"{animals:,}", "animals") if animals
           else _cell(f"{seen:,}", "slices with an animal")}
    {_cell(f"{taxa}", "names given")}
  </div>

  <section class="warning" id="warning">
    <p class="alarm"><b>Disclaimer</b> Nothing here has been checked by anyone who
       knows these animals. Every name is one detector's guess on footage it was
       never trained on, most frames were never read at all, and every count is an
       upper bound.</p>
    <details class="caveats">
      <summary>what exactly is wrong with it</summary>
      <div class="warn-grid">
        <div>
          <h3>A proof of concept</h3>
          <p>A demonstration that an archive of unwatched video can be read, indexed
             and made browsable. Not a survey.</p>
        </div>
        <div>
          <h3>The names are guesses</h3>
          <p>One YOLOv5 checkpoint trained on MBARI imagery, 499 classes, run on
             frames it has never seen. On a set of labelled specimens it named two of
             five correctly and was wrong at 0.94 confidence on the rest. Read a name
             as a shortlist.</p>
        </div>
        <div>
          <h3>Most frames were never read</h3>
          <p>A sample of each expedition's dives, a sample of each dive's bottom
             time, and the detector looking about once a second. The table below says
             how much of each expedition that was.</p>
        </div>
        <div>
          <h3>Counts are upper bounds</h3>
          <p>Sightings are linked into animals geometrically, which against a
             tracking benchmark splits the average animal across about 1.8 entries.
             The long tail is single, low-confidence detections.</p>
        </div>
        <div>
          <h3>The picture is a proxy</h3>
          <p>Almost all of this footage is published at 640×360, some older tapes at
             360×240 - about six points of recall below full resolution, and no
             upscaling gets it back.</p>
        </div>
        <div>
          <h3>Colour is the vehicle's</h3>
          <p>Everything below the photic zone is lit by the lamps that filmed it, and
             water takes the red out within metres. Colour describes the lighting as
             much as the animal.</p>
        </div>
      </div>
    </details>
  </section>

  <section class="explore" id="explore">
    <header class="explore-head">
      <p class="chapter">02 / what is in it</p>
      <h2>Everything that was found</h2>
      <p class="lede">Grouped by phylum, the way the reports are. Click a ring to go
         further in and the middle of it to come back out. Hover a picture to watch
         the seconds around the animal in it.</p>
      <nav class="jumps" id="jumps" aria-label="the phyla this collection found"></nav>
      <nav class="crumbs" id="crumbs" aria-label="the branch in focus"></nav>
    </header>
    <div class="explore-body">
      <div class="sun-side">
        <p class="sun-read" id="sunRead"></p>
        <div class="sun"><svg id="sunburst" viewBox="0 -100 100 200" role="img"
             preserveAspectRatio="xMinYMid meet"
             aria-label="the taxonomy of what was found, as half a sunburst"></svg>
        </div>
        <aside class="about" id="about"></aside>
      </div>
      <div class="wall" id="wall">
        <p class="empty">Loading what was found…</p>
        <div class="sentinel" id="more"></div>
      </div>
    </div>
  </section>

  <section class="fleet">
    <p class="chapter">03 / the expeditions</p>
    <h2>Where the footage came from</h2>
    <table class="fleet-table">
      <thead><tr><th>expedition</th><th>when</th><th>analysed</th><th>footage</th>
        <th>animals</th><th>names</th><th></th></tr></thead>
      <tbody>{"".join(_fleet_row(r) for r in rows)}</tbody>
    </table>
    <p class="note">“Analysed” is recordings read of recordings published. Nothing here
       was sampled at random: the deepest dives were chosen, and within them the hours
       the vehicle was on the bottom.</p>
  </section>

  <section class="credits">
    <p class="chapter">04 / whose work this is</p>
    <div class="credit-grid">
      <div>
        <h3>The footage</h3>
        <p>Every frame was filmed, published and paid for by somebody else. NOAA Ocean
           Exploration's Okeanos Explorer cruises and its earlier programmes are in the
           public domain; MBARI's DeepSea-MOT sequences are CC BY; the Axial Seamount
           camera belongs to the Ocean Observatories Initiative's Regional Cabled Array,
           run by the University of Washington. Each expedition above links to its own
           archive, which is the thing to cite.</p>
      </div>
      <div>
        <h3>The detector</h3>
        <p>A YOLOv5 checkpoint trained by <a href="https://fathomnet.org/">FathomNet</a>
           on MBARI imagery - weights CC BY 4.0, and the code that loads them is
           GPL-3.0. It knows 499 classes and was never trained on most of this footage.
           Nothing here is a model this project built.</p>
      </div>
      <div>
        <h3>The names</h3>
        <p>Ranks and identifiers come from the
           <a href="https://www.marinespecies.org/">World Register of Marine Species</a>,
           resolved once per class name; article titles from
           <a href="https://en.wikipedia.org/">Wikipedia</a>, and a name is linked there
           only where it actually has an article. Nothing on this page describes an
           animal in its own words.</p>
      </div>
      <div>
        <h3>This tool</h3>
        <p><a href="https://github.com/ida-mdc/pixel-patrol">Pixel Patrol</a> reads
           image and video collections and reports what is in them; this is its
           deep-sea extension. The drawing is of Pixel Patrol herself, gone diving.</p>
      </div>
    </div>
  </section>

  <footer>
    Written by <code>python -m pixel_patrol_deepsea.collect site</code> on
    {datetime.now().strftime('%Y-%m-%d %H:%M')}. Serve this folder over HTTP and every
    link works; there is nothing else to install.
  </footer>
</main>
<script>const LOOKUP = {_lookup(index)};</script>
<script>{SCRIPT}</script>
</body></html>"""


def _cell(value: str, label: str) -> str:
    """One figure in the readout strip under the header.

    Hairline-separated figures in a monospace column rather than a grid of rounded
    cards: this is the instrument reading, and it should look like one.
    """
    return (f'<div class="cell"><b>{html.escape(value)}</b>'
            f'<span>{html.escape(label)}</span></div>')


def _together(url: str) -> str:
    """The way into the one report that spans the collection.

    It is not another gallery, and saying "open all nine expeditions" did not warn
    anybody of that: it is the statistics over every expedition that has been read,
    and it carries no pictures at all, because those are hundreds of megabytes and
    belong in an expedition's own report. It opens grouped by expedition, since the
    only reason to put them in one report is to compare them.
    """
    if not url:
        return ""
    return (f'<a class="cta" href="{html.escape(url, quote=True)}">'
            f'the statistics, every expedition together &rarr;</a>'
            f'<p class="cta-note">Counts, names, depths, positions and times, grouped '
            f'by expedition. No pictures - they are in each expedition\'s own report, '
            f'listed below.</p>')


def _lookup(index: Optional[Dict]) -> str:
    """The encyclopaedia articles that exist, for the names this collection holds.

    Only what `fetch_wikipedia` actually found, so the page never offers a link to
    an article that is not there, and only for the names in this tree, so a
    collection of one midwater dive carries no entry about barnacles.
    """
    from pixel_patrol_deepsea.fetch_wikipedia import load_articles

    names = set()

    def walk(node: Dict) -> None:
        names.add(node.get("name") or "")
        for child in node.get("children") or []:
            walk(child)

    if index:
        walk(index.get("tree") or {})
        for name, about in (index.get("taxa") or {}).items():
            names.add(name)
            names.update(about.get("above") or [])
    articles = load_articles()
    return json.dumps({name: articles[name] for name in sorted(names)
                       if name in articles}, separators=(",", ":"))


def _mission(row) -> str:
    """The expedition's own page, where it has one that still answers."""
    link = getattr(row, "link", "")
    if not link:
        return ""
    return (f'<a class="mission" href="{html.escape(link, quote=True)}" '
            f'title="what the ship was doing">mission</a> ')


def _fleet_row(row) -> str:
    from pixel_patrol_deepsea.catalogue_page import _clock, _link

    share = (row.processed / row.listed * 100) if row.listed else 0
    taxa = f"{len(row.taxa)}" if row.taxa else "—"
    # An expedition nobody has listed or read yet has no denominator, and "0 of 0 ·
    # 0%" reads as a measurement rather than as the absence of one.
    read = (f'{row.processed:,}<span class="sub">of {row.listed:,} · {share:.0f}%</span>'
            if row.listed else (f"{row.processed:,}" if row.processed else "—"))
    animals = (f"{row.animals:,}" if row.animals else
               f'{row.with_animals:,}<span class="sub">slices</span>'
               if row.with_animals else "—")
    return f"""<tr>
      <td><b>{html.escape(row.title)}</b><span class="sub">{html.escape(row.id)}</span></td>
      <td class="num">{html.escape(row.date or '—')}</td>
      <td class="num">{read}</td>
      <td class="num">{_clock(row.seconds)}</td>
      <td class="num">{animals}</td>
      <td class="num">{taxa}</td>
      <td class="num">{_mission(row)}{_link(row)}</td>
    </tr>"""


STYLE = """
:root {
  --abyss: #04101c; --abyss-2: #071a2b; --ink: #e8f4ff; --dim: #89a5bc;
  /* The accent is the colour of the footage itself. Every metre of depth takes
     more of the red out, so a deep-sea frame is cyan whatever is in it - measured
     across this collection, the hue sits between 150 and 240 degrees. */
  --glow: #35d6f5; --warn: #ffb020;
  --line: rgba(140,190,220,.16); --card: rgba(255,255,255,.03);
  /* Figures, labels and stamps are monospace throughout: this is a record of work
     and it should read like the log it is. */
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
body { margin: 0; background: var(--abyss); color: var(--ink);
       font: 16px/1.65 system-ui, -apple-system, "Segoe UI", sans-serif;
       -webkit-font-smoothing: antialiased; }
h1, h2, h3 { line-height: 1.12; letter-spacing: -.015em; margin: 0; font-weight: 600; }
a { color: var(--glow); }

/* ── the header stamp ─────────────────────────────────────────────────────── */
.bar { display: flex; justify-content: space-between; gap: 1rem; align-items: baseline;
       padding: .7rem 6vw; border-bottom: 1px solid var(--line);
       font: .68rem/1 var(--mono); letter-spacing: .16em; text-transform: uppercase;
       color: var(--dim); background: var(--abyss-2); }
.bar b { color: var(--glow); font-weight: 600; letter-spacing: .18em; }
.bar .stamp { font-variant-numeric: tabular-nums; text-align: right; }

/* ── 01, and the figures under it ─────────────────────────────────────────── */
.chapter { font: .68rem/1 var(--mono); letter-spacing: .2em; text-transform: uppercase;
           color: var(--glow); margin: 0 0 1.1rem; }
.chapter::before { content: ""; display: inline-block; width: 2rem; height: 1px;
                   background: var(--glow); vertical-align: .28em; margin-right: .7rem;
                   opacity: .55; }
.hero { display: grid; grid-template-columns: minmax(0, 24rem) minmax(0, 1fr);
        align-items: center; gap: 3.5rem; padding: 3.5rem 6vw 3rem;
        background:
          radial-gradient(90% 120% at 8% 0%, #0c2b44 0%, transparent 62%),
          linear-gradient(180deg, var(--abyss-2), var(--abyss) 78%); }
.hero-art { justify-self: center; max-width: 24rem; }
/* Black line art on white, which is the wrong way round down here. Inverted, the
   paper is very nearly black but not quite - and `screen` lifted that last level or
   two, which on a page this dark is a visible rectangle around the drawing. The
   contrast step clips the paper to exactly black, where screening it is a no-op. */
.hero-art img { width: 100%; height: auto;
                filter: invert(1) contrast(1.2) brightness(1.04);
                mix-blend-mode: screen; opacity: .92; }
.hero-say { max-width: 38rem; }
.hero h1 { font-size: clamp(1.9rem, 3.4vw, 2.7rem); }
.lede { color: var(--dim); max-width: 34rem; }
.hero .lede { margin: 1.2rem 0 1.8rem; }
.cta { display: inline-block; font: .72rem/1 var(--mono); letter-spacing: .14em;
       text-transform: uppercase; text-decoration: none; color: var(--glow);
       border: 1px solid var(--glow); border-left-width: 3px; padding: .7rem 1rem;
       background: rgba(53,214,245,.06); }
.cta:hover { background: rgba(53,214,245,.16); }
.cta-note { color: var(--dim); font-size: .84rem; margin: .8rem 0 0; max-width: 30rem; }
.readout { display: grid; grid-template-columns: repeat(4, 1fr);
           border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.readout .cell { padding: .9rem 1.4rem; border-left: 1px solid var(--line); }
.readout .cell:first-child { border-left: 0; padding-left: 6vw; }
.readout b { display: block; font: 1.4rem/1.2 var(--mono); letter-spacing: -.02em;
             font-variant-numeric: tabular-nums; }
.readout span { display: block; margin-top: .35rem; color: var(--dim);
                font: .64rem/1 var(--mono); letter-spacing: .16em;
                text-transform: uppercase; }

/* ── the disclaimer ───────────────────────────────────────────────────────── */
/* Six headed paragraphs of caveat, above the fold, was more words than the page
   itself and read as a legal notice - which is how a legal notice gets skipped.
   One line in the warning colour is harder to miss than six, and the six are still
   here for whoever opens them. */
.warning { padding: 2rem 6vw; }
.alarm { margin: 0; padding: .85rem 1.1rem; color: #ffe9c9; max-width: 66rem;
         border: 1px solid rgba(255,176,32,.4); border-left: 3px solid var(--warn);
         background: rgba(255,176,32,.07); font-size: .95rem; }
.alarm b { color: var(--warn); font: .68rem/1 var(--mono); letter-spacing: .18em;
           text-transform: uppercase; margin-right: .7rem; }
.caveats { margin-top: .8rem; }
.caveats summary { color: var(--dim); cursor: pointer; width: fit-content;
                   font: .68rem/1 var(--mono); letter-spacing: .12em;
                   text-transform: uppercase; }
.caveats summary:hover { color: var(--ink); }
.warn-grid { display: grid; gap: 1.3rem 2.4rem; margin-top: 1.3rem;
             grid-template-columns: repeat(auto-fit, minmax(17rem, 1fr)); }
.warn-grid h3 { font-size: .95rem; margin-bottom: .3rem; }
.warn-grid p { color: var(--dim); margin: 0; font-size: .91rem; }

/* ── 02 the taxonomy, and the contact sheet beside it ─────────────────────── */
.explore { padding: 3rem 6vw; border-top: 1px solid var(--line); }
.explore h2, .fleet h2 { font-size: clamp(1.3rem, 2.6vw, 1.9rem); }
.explore-head .lede { margin: .7rem 0 1.2rem; }
.jumps { display: flex; flex-wrap: wrap; gap: .4rem; margin: 0 0 .8rem; }
.jumps .jump, .crumbs button {
  background: transparent; color: var(--dim); border: 1px solid var(--line);
  border-radius: 2px; padding: .32rem .6rem; cursor: pointer;
  font: .72rem/1 var(--mono); letter-spacing: .04em; }
.jumps .jump span { color: var(--ink); margin-left: .5rem;
                    font-variant-numeric: tabular-nums; }
.jumps .jump:hover, .crumbs button:hover { border-color: var(--glow); color: var(--ink); }
.jumps .jump.on { border-color: var(--glow); color: var(--ink);
                  background: rgba(53,214,245,.1); }
.jumps .jump.odd { border-color: rgba(255,176,32,.45); color: #e9bd7c; }
.jumps .jump.odd span { color: #f0c274; }
.jumps .all { border-style: dashed; }
.crumbs { display: flex; flex-wrap: wrap; gap: .3rem; align-items: center;
          margin-bottom: 1.2rem; min-height: 1.6rem; }
.crumbs .sep { color: #44596b; font: .72rem var(--mono); }
.crumbs .only { border-color: #4a5f72; }

/* A third of the page for the ring, measured from the edge it starts at. The ring
   is half a circle with its flat side on the page's own left border: a whole circle
   in this column is small and surrounded by nothing, and the half is the same
   drawing at twice the radius. */
.explore-body { display: grid; grid-template-columns: minmax(200px, .85fr) minmax(0, 2.4fr);
                gap: 2rem; align-items: start; }
.sun { position: relative; margin-left: -6vw; }
/* Half a circle is twice as tall as it is wide, so it is the window and not the
   column that decides how big the ring gets - and the column is cut to suit it. */
.sun svg { width: 100%; height: auto; display: block; max-height: min(76vh, 40rem); }
.sun path { cursor: pointer; transition: opacity .15s; }
.sun path:hover { opacity: .78; }
/* The hole is the way back up. Drawn as a target rather than left as empty space,
   because the ring it sits in is the one thing on the page that goes deeper. */
.sun .hole { fill: rgba(53,214,245,.05); stroke: var(--line); stroke-width: .5;
             cursor: default; }
.sun .hole.up { fill: rgba(53,214,245,.12); cursor: pointer; }
.sun .hole.up:hover { fill: rgba(53,214,245,.22); }
.sun .back { fill: var(--glow); font-family: var(--mono); font-size: 5px;
             letter-spacing: .3px; pointer-events: none; }
.sun-read { margin: 0 0 .4rem; min-height: 2rem; }
.sun-read b { font: 1.3rem/1.2 var(--mono); font-variant-numeric: tabular-nums;
              margin-right: .5rem; }
.sun-read span { color: var(--dim); font-size: .9rem; }
.sun-read.hovering span { color: var(--ink); }

/* The pictures scroll in their own box. A wall that grows without end grows the
   page without end: everything below it was unreachable once a few thousand animals
   had loaded, and scrolling anywhere near the ring fetched more of them. */
.wall { max-height: min(76vh, 40rem); overflow-y: auto; overscroll-behavior: contain;
        border: 1px solid var(--line); padding: 6px; background: #020a12;
        display: grid; gap: 6px; align-content: start;
        grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
        scrollbar-width: thin; scrollbar-color: #2a4356 transparent; }
.wall .empty { color: var(--dim); grid-column: 1 / -1; font-size: .9rem; }
.tile { position: relative; margin: 0; overflow: hidden; background: #00121f;
        aspect-ratio: 1; cursor: pointer; align-self: start; }
/* The crops are whatever size the animal was on screen. Fill the tile from the
   column width - stretched in both axes the aspect ratio is ignored and a row of
   small crops collapses. */
.tile img { position: absolute; inset: 0; width: 100%; height: 100%;
            object-fit: cover; display: block; }
/* How sure the detector was and how long the animal stayed in view at the top, the
   name at the bottom, and nothing at all until the pointer is on it. */
.tile figcaption, .tile .meta { position: absolute; opacity: 0;
            transition: opacity .12s; padding: .3rem .4rem; color: #dcefff; }
.tile figcaption { inset: auto 0 0; font-size: .68rem; line-height: 1.2;
            background: linear-gradient(transparent, rgba(0,8,16,.94)); }
.tile .meta { inset: 0 0 auto; display: flex; justify-content: space-between;
            font: .64rem/1 var(--mono); letter-spacing: .04em;
            background: linear-gradient(rgba(0,8,16,.9), transparent); }
.tile .meta b { color: var(--glow); font-variant-numeric: tabular-nums; }
.tile .meta span { color: #bad4e7; font-variant-numeric: tabular-nums; }
.tile:hover figcaption, .tile:hover .meta,
.tile:focus-within figcaption, .tile:focus-within .meta { opacity: 1; }
.sentinel { grid-column: 1 / -1; height: 1px; }
.about { margin-top: 1.2rem; border: 1px solid var(--line); border-radius: 2px;
         padding: .9rem 1rem; background: var(--card); }
.about h3 { font-size: 1.05rem; margin-bottom: .25rem; }
.about .rank { color: var(--dim); font: .62rem var(--mono); letter-spacing: .14em;
               text-transform: uppercase; margin-left: .4rem; }
.about .count { color: var(--glow); margin: 0 0 .7rem;
                font: .8rem var(--mono); font-variant-numeric: tabular-nums; }
.about .hint { color: var(--dim); font-size: .88rem; margin: 0 0 .6rem; }
.about .links { display: flex; flex-direction: column; gap: .4rem; margin: 0; }
.about .links a { font: .72rem var(--mono); letter-spacing: .04em;
                  text-decoration: none; }
.about .links a:hover { text-decoration: underline; }

/* ── 03 the expeditions ───────────────────────────────────────────────────── */
.fleet { padding: 3rem 6vw; border-top: 1px solid var(--line); }
.fleet h2 { margin-bottom: 1.2rem; }
.fleet-table { width: 100%; border-collapse: collapse; font-size: .92rem; }
.fleet-table th { text-align: left; color: var(--dim); font-weight: 400;
                  font: .64rem var(--mono); letter-spacing: .14em;
                  text-transform: uppercase;
                  border-bottom: 1px solid var(--line); padding: .5rem .6rem; }
.fleet-table td { border-bottom: 1px solid var(--line); padding: .6rem;
                  vertical-align: top; }
.fleet-table .num { white-space: nowrap; font-family: var(--mono); font-size: .85rem;
                    font-variant-numeric: tabular-nums; }
.fleet-table .sub { display: block; color: var(--dim); font-size: .74rem;
                    font-family: var(--mono); }
.fleet-table a { text-decoration: none; border: 1px solid var(--line); border-radius: 2px;
                 padding: .25rem .55rem; font-size: .72rem; white-space: nowrap; }
.fleet-table a:hover { border-color: var(--glow); }
.note { color: var(--dim); font-size: .85rem; margin-top: 1rem; max-width: 46rem; }

/* ── 04 credit ────────────────────────────────────────────────────────────── */
.credits { padding: 3rem 6vw; border-top: 1px solid var(--line);
           background: linear-gradient(180deg, rgba(53,214,245,.04), transparent 55%); }
.credit-grid { display: grid; gap: 1.4rem 2.4rem;
               grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); }
.credit-grid h3 { font-size: .78rem; margin-bottom: .4rem; color: var(--glow);
                  font-family: var(--mono); letter-spacing: .14em;
                  text-transform: uppercase; font-weight: 400; }
.credit-grid p { color: var(--dim); font-size: .89rem; margin: 0; }
a.mission { margin-right: .35rem; }
footer { padding: 2rem 6vw 3.5rem; color: var(--dim); font-size: .82rem;
         border-top: 1px solid var(--line); }
code { background: var(--card); padding: .1rem .35rem; font-family: var(--mono);
       font-size: .78rem; }

@media (max-width: 1100px) {
  .readout { grid-template-columns: repeat(2, 1fr); }
  .readout .cell:nth-child(3) { border-left: 0; padding-left: 6vw; }
}
@media (max-width: 900px) {
  .hero { grid-template-columns: 1fr; gap: 1.5rem; padding: 2rem 6vw; }
  .hero-art { max-width: 15rem; }
  .explore-body { grid-template-columns: 1fr; }
  .sun svg { max-height: 22rem; }
  /* One column, and the page's own scroll is the only one worth having. */
  .wall { max-height: none; overflow: visible; }
}
"""


SCRIPT = r"""
/* The page is a browser over the store `tiles` wrote: an index of the taxonomy,
   and beside it a directory per taxon holding pages of animals. Nothing is built
   into the HTML, so the page does not grow with the collection - what is on screen
   is what has been fetched. */
const TILES = 'tiles';
const SVGNS = 'http://www.w3.org/2000/svg';
/* The ring's geometry, in the units the sunburst is drawn in. The hole is wide
   because it is a button - the way back out - and not just the middle. Every wedge
   reaches RIM whether the naming got that deep or not, so the drawing is the same
   half circle at every level. */
const HOLE = 26, RING = 26, RINGS = 3;
const RIM = HOLE + RING * RINGS - 2;
/* The two branches that are not taxonomy: the register's names run out here. */
const UNPLACED = 'Unplaced', UNDECIDED = 'Undecided';
/* Below this a wedge is thinner than its own outline and not worth a path. */
const SLIVER = 0.004;

/* The only things this page says in its own words, because they are its own words:
   names this project gave, not animals somebody else described. */
const OURS = {
  Undecided: `The detector saw this animal several times and called it something
    different each time, with no rank the guesses shared - a fish one second, a
    sponge the next. Rather than pick the loudest, the name was dropped. Worth a
    look: this is where the detector is least sure.`,
  Unplaced: `Names the World Register of Marine Species does not carry: the
    detector's own classes, and the animals whose looks could not be reconciled.`,
};

const state = { index: null, path: [], taxa: [], queue: [], loading: false,
                done: false, pages: new Map(), clips: new Map() };

const el = (id) => document.getElementById(id);

const say = (n) => n.toLocaleString();

/* Wide enough for the pictures to scroll in their own box, or narrow enough that
   the page's own scroll is the only one worth having. */
const paged = () => window.matchMedia('(min-width: 900px)').matches;

async function boot() {
  try {
    state.index = await (await fetch(`${TILES}/index.json`)).json();
  } catch (err) {
    el('wall').innerHTML = '<p class="empty">No pictures were written beside this page. '
      + 'Run <code>collect site</code> where the reports are.</p>';
    return;
  }
  drawJumps();
  focusOn(...fromHash());
  watchTheWall();
}

/* ── the taxonomy, as rings ────────────────────────────────────────────────── */

/** The node the path names, walking down from the root. */
function nodeAt(path) {
  let node = state.index.tree;
  for (const step of path) {
    node = (node.children || []).find(child => child.name === step) ?? node;
  }
  return node;
}

/** Every taxon under a node - the leaves whose pages the wall will read. */
function taxaUnder(node) {
  const found = [...(node.taxa || [])];
  for (const child of node.children || []) found.push(...taxaUnder(child));
  return found;
}

const RING_COLOURS = ['#35d6f5', '#4cc9f0', '#4895ef', '#5e7ce2', '#7b6cf6', '#9b5de5',
                      '#c05299', '#e5678b', '#f4845f', '#f7b267'];

function colourFor(name, depth) {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const base = RING_COLOURS[hash % RING_COLOURS.length];
  return depth === 0 ? base : shade(base, 1 - depth * 0.18);
}

function shade(hex, factor) {
  const n = parseInt(hex.slice(1), 16);
  const rgb = [n >> 16, (n >> 8) & 255, n & 255]
    .map(v => Math.max(0, Math.min(255, Math.round(v * factor))));
  return `rgb(${rgb.join(',')})`;
}

/** An SVG arc between two angles, at one ring's radius. */
function arc(from, to, inner, outer) {
  const point = (angle, r) => [r * Math.sin(angle), -r * Math.cos(angle)];
  const [x0, y0] = point(from, outer), [x1, y1] = point(to, outer);
  const [x2, y2] = point(to, inner), [x3, y3] = point(from, inner);
  const wide = to - from > Math.PI ? 1 : 0;
  return `M${x0} ${y0}A${outer} ${outer} 0 ${wide} 1 ${x1} ${y1}`
       + `L${x2} ${y2}A${inner} ${inner} 0 ${wide} 0 ${x3} ${y3}Z`;
}

/** One path in the ring: what it looks like, what it says, and where it goes. */
function paint(d, { fill, opacity, stroke, width, title, reads, go, cls }) {
  const path = document.createElementNS(SVGNS, 'path');
  path.setAttribute('d', d);
  path.setAttribute('fill', fill);
  path.setAttribute('fill-opacity', String(opacity));
  path.setAttribute('stroke', stroke || '#04101c');
  path.setAttribute('stroke-width', String(width || 0.6));
  if (cls) path.setAttribute('class', cls);
  const label = document.createElementNS(SVGNS, 'title');
  label.textContent = title;
  path.appendChild(label);
  path.addEventListener('mouseenter', () => readout(reads));
  path.addEventListener('mouseleave', () => readout(null));
  if (go) path.addEventListener('click', go);
  el('sunburst').appendChild(path);
}

/* The colour of a wedge that goes no deeper. Grey rather than absent: a ring that
   stopped where the naming stopped left the drawing a ragged three-quarters of a
   half circle, and a reader wondering what ate the rest. Everywhere it is grey, the
   answer is that these animals were named at that rank and no further. */
const UNRESOLVED = '#3d566b';

/** A wedge where the naming ran out, from its depth all the way to the rim. */
function unresolved(from, to, depth, title, reads, go) {
  if (depth >= RINGS) return;
  paint(arc(from, to, HOLE + depth * RING, RIM), {
    fill: UNRESOLVED, opacity: 0.34, width: 0.6, cls: 'rest', title, reads, go });
}

/** One branch's arc, and the grey beyond it when nothing under it has a name. */
function wedge(node, from, to, depth, here) {
  const inner = HOLE + depth * RING;
  paint(arc(from, to, inner, inner + RING - 2), {
    fill: colourFor(node.name, depth), opacity: 0.92 - depth * 0.18,
    stroke: here ? '#7fd4ff' : '#04101c', width: here ? 1.6 : 0.6,
    title: `${node.name} — ${say(node.count)}`, reads: node,
    go: () => focusOn(pathOf(node)) });
  if (!(node.children || []).length) {
    unresolved(from, to, depth + 1,
      `${node.name} — ${say(node.count)}, as deep as the naming goes`,
      node, () => focusOn(pathOf(node)));
  }
}

/** The angle a node keeps for itself: animals named at its rank and no deeper.
 *
 * Called Porifera and nothing more. It is often the commonest answer the detector
 * gives, and it fills out to the rim like anything else that goes no further. */
function leftover(node, from, to, depth) {
  const kids = node.children || [];
  if (!kids.length) return;
  const rest = node.count - kids.reduce((sum, child) => sum + child.count, 0);
  if (rest <= 0) return;
  const reads = { name: `${node.name}, no finer name`, count: rest };
  unresolved(from, to, depth, `${reads.name} — ${say(rest)}`, reads,
             () => focusOn(pathOf(node), node.name));
}

/* ── the address bar ───────────────────────────────────────────────────────── */

/** Where the URL says to be: `#t=Animalia/Porifera`, or `!Porifera` for a rest arc.
 *
 * A branch worth showing someone is worth being able to send them - most of all
 * Undecided, which is the one anybody will want to argue about. */
function fromHash() {
  const raw = decodeURIComponent((location.hash.match(/^#t=(.*)$/) || [])[1] || '');
  if (!raw) return [[]];
  const [trail, only] = raw.split('!');
  const path = trail.split('/').filter(Boolean);
  return [path, only || null];
}

function toHash(path, only) {
  const trail = path.join('/') + (only ? `!${only}` : '');
  history.replaceState(null, '', trail ? `#t=${encodeURIComponent(trail)}` : location.pathname);
}

/** One way in per phylum, plus the whole collection and the names that are not taxa.
 *
 * This row used to be the four biggest branches anywhere in the tree, which put a
 * kingdom, a phylum and a class beside each other - three different ranks, in an
 * order nothing explained, and no way back to everything. Phylum is the rank the
 * reports group and colour by, and the one a reader actually navigates by: sponges,
 * cnidarians, fish. So the row is every phylum this collection turned up, biggest
 * first, and the two branches that are not phyla are marked rather than mixed in.
 *
 * Undecided is one of those and is not a failure to be hidden: it is every animal
 * the detector called one thing and then another, which is where its confusion is
 * legible. */
function drawJumps() {
  const jumps = el('jumps');
  if (!jumps) return;
  const root = state.index.tree;
  const phyla = [], odd = [];
  for (const kingdom of root.children || []) {
    if (kingdom.name === UNPLACED) {
      odd.push({ node: kingdom, path: [kingdom.name] });
      for (const child of kingdom.children || []) {
        if (child.name === UNDECIDED) odd.push({ node: child, path: [kingdom.name, child.name] });
      }
      continue;
    }
    // A phylum is the step below the kingdom, whatever depth the register's own
    // intermediate ranks would have put it at.
    for (const phylum of kingdom.children || []) {
      phyla.push({ node: phylum, path: [kingdom.name, phylum.name] });
    }
  }
  phyla.sort((a, b) => b.node.count - a.node.count);
  const all = { node: root, path: [], all: true };
  jumps.replaceChildren(...[all, ...phyla, ...odd].map(jumpFor));
  markJumps();
}

function jumpFor({ node, path, all }) {
  const button = document.createElement('button');
  button.className = 'jump' + (all ? ' all' : '')
    + (node.name === UNDECIDED || node.name === UNPLACED ? ' odd' : '');
  button.dataset.path = path.join('/');
  button.innerHTML = `${escape(all ? 'everything' : node.name)} <span>${say(node.count)}</span>`;
  button.addEventListener('click', () => {
    focusOn(path);
    el('explore').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  return button;
}

/** Light the button the wall is showing, so the row says where you are and not
 *  only where you could go. A branch deeper than a phylum lights its phylum. */
function markJumps() {
  const here = state.path.join('/');
  for (const button of el('jumps').children) {
    const path = button.dataset.path;
    const on = path ? here === path || here.startsWith(path + '/') : !here;
    button.classList.toggle('on', on);
  }
}

/** The path from the root down to a node, for a click on any ring. */
function pathOf(node) {
  const from = state.basePath || [];
  const base = nodeAt(from);
  if (node === base) return from;
  return [...from, ...(pathTo(base, node) || [])];
}

/** Three rings out from whatever is in focus, each child an arc of its parent.
 *
 * Half a circle, from straight up round to straight down, with the flat side on
 * the page's left edge. The same rings at twice the radius in a third of the width,
 * and the hole becomes big enough to be the button it ought to have been. */
function drawSun() {
  const svg = el('sunburst');
  const focus = nodeAt(state.path);
  // A leaf has no rings of its own. Draw its parent's and light the leaf up, so
  // the reader keeps the context instead of staring at an empty circle.
  const leaf = !(focus.children || []).length && state.path.length > 0;
  state.basePath = leaf ? state.path.slice(0, -1) : state.path;
  const base = nodeAt(state.basePath);
  svg.innerHTML = '';
  const place = (node, from, to, depth) => {
    if (depth >= RINGS || to - from < SLIVER) return;
    // The share of the *whole* wedge, not of what is left of it. Measuring against
    // the remainder shrank every child after the first - at the root, Animalia took
    // its honest four fifths and the two kingdoms after it got a fifth of a fifth
    // each, so the ring stopped at 151 degrees of 180 and the drawing came out as
    // some fraction of a half circle that changed with every branch.
    const whole = to - from, held = Math.max(1, node.count);
    let at = from;
    for (const child of node.children || []) {
      const span = whole * (child.count / held);
      if (span >= SLIVER) {
        wedge(child, at, at + span, depth, leaf && child.name === focus.name);
        place(child, at, at + span, depth + 1);
      }
      at += span;
    }
    if (to - at > SLIVER) leftover(node, at, to, depth);
  };
  place(base, 0, Math.PI, 0);
  drawHole();
  svg.setAttribute('viewBox', `-1 ${-RIM - 4} ${RIM + 5} ${2 * RIM + 8}`);
  readout(null);
  drawAbout(focus);
}

/** The half-disc at the flat side: a click in it is a step back out.
 *
 * The report's own sunburst has done this since it was a sunburst, and a reader
 * who has used one goes for the middle first. The breadcrumbs above do the same
 * thing for a keyboard and for jumping more than one step. */
function drawHole() {
  const up = state.only ? state.path : state.path.slice(0, -1);
  const somewhere = Boolean(state.only || state.path.length);
  const path = document.createElementNS(SVGNS, 'path');
  path.setAttribute('d', `M0 ${-HOLE}A${HOLE} ${HOLE} 0 0 1 0 ${HOLE}Z`);
  path.setAttribute('class', somewhere ? 'hole up' : 'hole');
  const title = document.createElementNS(SVGNS, 'title');
  title.textContent = somewhere
    ? `back to ${up.length ? up[up.length - 1] : 'everything'}`
    : 'the whole collection - click a ring to go in';
  path.appendChild(title);
  if (somewhere) path.addEventListener('click', () => focusOn(up));
  el('sunburst').appendChild(path);
  if (!somewhere) return;
  const label = document.createElementNS(SVGNS, 'text');
  label.setAttribute('class', 'back');
  label.setAttribute('x', '4');
  label.setAttribute('y', '2');
  label.textContent = '‹ BACK';
  el('sunburst').appendChild(label);
}

/** The line above the ring reads whatever the pointer is over, or the focus. */
function readout(node) {
  const focus = nodeAt(state.path);
  const shown = node || focus;
  el('sunRead').innerHTML = `<b>${say(shown.count)}</b><span>${shown.name === 'everything'
    ? 'animals, everything together' : escape(shown.name)}</span>`;
  el('sunRead').classList.toggle('hovering', Boolean(node));
}

/** Where to read about this branch - and nothing this page made up.
 *
 * The register is the record: somebody meeting `Asbestopluma` should land on the
 * people whose business it is, by identifier rather than by search. The
 * encyclopaedia is what actually describes the animal, and it is offered only where
 * `fetch_wikipedia` found an article, so the link is never a guess. The title it
 * found is worth showing as well, since `Holothuroidea` resolves to *Sea cucumber*,
 * which is the word somebody was looking for.
 *
 * The two buckets that are not taxonomy get a sentence of our own, because they are
 * names this project and the detector made up and nobody else has to explain. */
function drawAbout(focus) {
  const about = el('about');
  const named = state.index.taxa[focus.name];   // set when the detector says this name
  if (focus.name === 'everything') {
    about.innerHTML = `<p class="hint">Every animal the detector found, arranged by
      what it called them. Click a ring to go in; the pictures follow.</p>`;
    return;
  }
  const under = taxaUnder(focus).length;
  about.innerHTML = `
    <h3>${escape(focus.name)}${named && named.rank
      ? `<span class="rank">${escape(named.rank)}</span>` : ''}</h3>
    <p class="count">${say(focus.count)} animals${under > 1 ? ` · ${under} names` : ''}</p>
    ${OURS[focus.name] ? `<p class="hint">${escape(OURS[focus.name])}</p>` : ''}
    ${links(focus, named)}`;
}

/** The register, and the encyclopaedia where it has an article. */
function links(focus, named) {
  if (focus.name === UNPLACED || focus.name === UNDECIDED) return '';
  if (state.path[0] === UNPLACED) {
    return `<p class="hint">One of the detector's own class names. The World Register
      of Marine Species carries no record of it, so there is nothing to look up.</p>`;
  }
  const worms = named && named.aphia
    ? `https://www.marinespecies.org/aphia.php?p=taxdetails&id=${named.aphia}`
    : `https://www.marinespecies.org/aphia.php?p=taxlist&searchpar=0&tComp=begins&tName=${
        encodeURIComponent(focus.name)}`;
  const article = LOOKUP[focus.name];
  return `<p class="links">
    <a href="${worms}" target="_blank" rel="noopener">${named && named.aphia
      ? 'WoRMS record' : 'find it in WoRMS'} &rarr;</a>
    ${article ? `<a href="https://en.wikipedia.org/wiki/${
      encodeURIComponent(article.replace(/ /g, '_'))}" target="_blank"
      rel="noopener">Wikipedia: ${escape(article)} &rarr;</a>` : ''}</p>`;
}

/** The steps from an ancestor down to a node, for a click three rings out. */
function pathTo(from, wanted, trail = []) {
  for (const child of from.children || []) {
    if (child === wanted) return [...trail, child.name];
    const deeper = pathTo(child, wanted, [...trail, child.name]);
    if (deeper) return deeper;
  }
  return null;
}

function drawCrumbs() {
  const crumbs = el('crumbs');
  crumbs.innerHTML = '';
  // At the root the trail is one step long and the button row already says so.
  if (!state.path.length && !state.only) return;
  const steps = ['everything', ...state.path];
  steps.forEach((step, depth) => {
    const button = document.createElement('button');
    button.textContent = depth === 0 ? 'everything' : step;
    button.addEventListener('click', () => focusOn(state.path.slice(0, depth)));
    crumbs.appendChild(button);
    if (depth < steps.length - 1 || state.only) {
      const sep = document.createElement('span');
      sep.className = 'sep'; sep.textContent = '›';
      crumbs.appendChild(sep);
    }
  });
  if (state.only) {
    const button = document.createElement('button');
    button.className = 'only';
    button.textContent = `just ${state.only}`;
    button.title = 'animals named no further than this - click to widen';
    button.addEventListener('click', () => focusOn(state.path));
    crumbs.appendChild(button);
  }
}

/* ── the wall ─────────────────────────────────────────────────────────────── */

/** Point the wall at a branch: its taxa, their pages, and nothing loaded yet. */
function focusOn(path, only = null) {
  state.path = path;
  state.only = only;
  const focus = nodeAt(path);
  const wanted = only ? [only] : taxaUnder(focus);
  state.taxa = wanted.filter(name => state.index.taxa[name]);
  // Round-robin the taxa rather than exhausting one: a reader who clicks Cnidaria
  // wants to see cnidarians, not four hundred of the commonest one first.
  state.queue = [];
  const pages = state.taxa.map(name => state.index.taxa[name].pages);
  for (let page = 0; page < Math.max(0, ...pages); page++) {
    state.taxa.forEach((name, i) => {
      if (page < pages[i]) state.queue.push({ taxon: name, page });
    });
  }
  state.done = false;
  toHash(path, only);
  const wall = el('wall');
  wall.replaceChildren(el('more'));
  wall.scrollTop = 0;
  drawSun();
  drawCrumbs();
  markJumps();
  loadMore();
}

async function loadMore() {
  if (state.loading || state.done) return;
  const next = state.queue.shift();
  if (!next) { state.done = true; return; }
  state.loading = true;
  try {
    const slug = state.index.taxa[next.taxon].slug;
    const { animals, stills } = await pageOf(slug, next.page);
    const wall = el('wall');
    let at = 0;
    for (const animal of animals) {
      const still = stills.slice(at, at + animal.l);
      at += animal.l;
      wall.insertBefore(tileFor(animal, still, slug, next.page), el('more'));
    }
  } catch (err) {
    /* a page that will not load is one page, not the end of the wall */
  } finally {
    state.loading = false;
  }
  // Keep filling until the end of the wall is out of reach, or a box taller than
  // its first page never asks for a second.
  if (roomForMore()) loadMore();
}

/** Is the end of the wall still within reach of what is on screen? */
function roomForMore() {
  const wall = el('wall');
  if (paged()) return wall.scrollHeight - wall.scrollTop - wall.clientHeight < 600;
  return el('more').getBoundingClientRect().top < window.innerHeight * 1.5;
}

/** Fetch the next page when the end of the wall comes into view.
 *
 * Watched inside the wall's own box where it has one, so scrolling the ring or the
 * page does not pull in pictures nobody asked for - and so the wall stops growing
 * the page, which is what put the expeditions below it out of reach. */
function watchTheWall() {
  const wall = el('wall');
  if (window.IntersectionObserver) {
    new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) loadMore();
    }, { root: paged() ? wall : null, rootMargin: '600px' }).observe(el('more'));
  }
  wall.addEventListener('scroll', () => { if (roomForMore()) loadMore(); });
}

/** A page of the store: its sizes, and the one blob holding its stills.
 *
 * Fetched once and kept, because the wall asks for the same page again when a
 * reader walks back up the taxonomy into a branch they have already seen. */
async function pageOf(slug, page) {
  const key = `${slug}/${page}`;
  if (!state.pages.has(key)) {
    state.pages.set(key, (async () => {
      const [animals, stills] = await Promise.all([
        fetch(`${TILES}/${slug}/p${page}.json`).then(r => r.json()),
        fetch(`${TILES}/${slug}/p${page}.jpgs`).then(r => r.arrayBuffer()),
      ]);
      return { animals, stills: new Uint8Array(stills) };
    })());
  }
  return state.pages.get(key);
}

/** The page's animations, fetched the first time any tile on it is hovered.
 *
 * One blob for sixty animals rather than sixty files: hovering the first tile of a
 * page pays for all of them, and a page nobody hovers costs nothing. */
async function clipsOf(slug, page) {
  const key = `${slug}/${page}`;
  if (!state.clips.has(key)) {
    state.clips.set(key, fetch(`${TILES}/${slug}/p${page}.clips`)
      .then(r => r.ok ? r.arrayBuffer() : new ArrayBuffer(0))
      .then(buffer => new Uint8Array(buffer))
      .catch(() => new Uint8Array(0)));
  }
  return state.clips.get(key);
}

/** Where in the page's clip blob one animal's frames begin. */
function clipStart(animals, wanted) {
  let at = 0;
  for (const animal of animals) {
    if (animal === wanted) return at;
    for (const size of animal.f || []) at += size;
  }
  return at;
}

const asPicture = (bytes) => URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }));

/** One frame of the contact sheet: the picture, and two facts over it.
 *
 * How sure the detector was and how long the animal stayed in view at the top, the
 * name at the bottom, both only while the pointer is on it. There is no play badge:
 * nearly every tile has a film, so a mark on nearly every tile said nothing and
 * cost a corner of the picture. */
function tileFor(animal, still, slug, page) {
  const figure = document.createElement('figure');
  figure.className = 'tile' + (animal.m ? ' moving' : '');
  const img = document.createElement('img');
  img.loading = 'lazy';
  img.decoding = 'async';
  img.alt = animal.t;
  img.src = asPicture(still);
  figure.appendChild(img);
  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.innerHTML = `<b>${animal.c.toFixed(2)}</b><span>${held(animal)}</span>`;
  figure.appendChild(meta);
  const caption = document.createElement('figcaption');
  caption.textContent = animal.t;
  figure.appendChild(caption);
  figure.title = `${animal.t} · ${animal.e} · ${clock(animal.s)} · ${animal.n} `
    + `look${animal.n === 1 ? '' : 's'}`
    + (animal.a < 1 ? ` · agreed ${Math.round(animal.a * 100)}%` : '');
  if (animal.m) animate(figure, img, slug, page, animal);
  return figure;
}

/** How long the animal was in view. One look is a moment, not a duration. */
function held(animal) {
  const seconds = animal.d || 0;
  return seconds >= 0.05 ? `${seconds.toFixed(1)}s` : '·';
}

/** The seconds around an animal, cut out of its page's clip blob on first hover. */
function animate(figure, img, slug, page, animal) {
  let frames = null, timer = null, still = img.src;
  const start = async () => {
    if (!frames) {
      try {
        const [{ animals }, blob] = await Promise.all([
          pageOf(slug, page), clipsOf(slug, page)]);
        let at = clipStart(animals, animal);
        frames = (animal.f || []).map(size => {
          const picture = asPicture(blob.slice(at, at + size));
          at += size;
          return picture;
        });
      } catch { frames = []; }
    }
    if (!frames.length || timer) return;
    let at = 0;
    timer = setInterval(() => { img.src = frames[at++ % frames.length]; }, 110);
  };
  const stop = () => { clearInterval(timer); timer = null; img.src = still; };
  figure.addEventListener('mouseenter', start);
  figure.addEventListener('focusin', start);
  figure.addEventListener('mouseleave', stop);
  figure.addEventListener('focusout', stop);
  figure.tabIndex = 0;
}

const escape = (text) => String(text).replace(/[<>&]/g, ch =>
  ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[ch]);

const clock = (seconds) => {
  const whole = Math.floor(seconds);
  return `${String(Math.floor(whole / 60)).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`;
};

boot();
"""
