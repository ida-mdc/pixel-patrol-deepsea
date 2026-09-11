"""The page a collection opens on.

Not a document about the collection - a way into it. The catalogue page it
replaces was a list: a wall of thirty species tiles built into the HTML, a card per
expedition, and a link. That is the right shape for a report of one dive and the
wrong one for tens of thousands of animals, where the question is not "what is the
best sponge" but "show me every sponge".

So this is four things, in the order someone meets them:

    the hero        what this is, in a sentence, over the numbers that say how much
                    of it there is - and moving pictures, because the first thing
                    anyone wants to know about deep-sea footage is what is in it.
    the warning     what the numbers are worth. It is above the fold on purpose and
                    it is not softened: a detector that is confidently wrong, a
                    fraction of the frames read, and a taxonomy resolved by an
                    algorithm rather than by anybody who knows the animals.
    the taxonomy    a sunburst of what was found, and beside it every picture of
                    whatever branch is clicked, loaded a screenful at a time from
                    the store `tiles` writes. It does not matter how many there
                    are; the page fetches what is on screen.
    the expeditions one line each: how much was analysed, and the way in.

Everything below the hero is built from `tiles/index.json` and the paged files
beside it, which is why the page is the same size whether the collection holds one
expedition or fifty.
"""

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

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
    # The catalogue lists more expeditions than have been read. The lede is about
    # what was read, and the table below is where the rest of them are accounted for.
    ran = len([r for r in rows if r.processed])
    combined = _report_url(f"../parquet/{EVERYTHING}.parquet") if len(everything) > 1 else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deep-sea footage collection</title>
<style>{STYLE}</style></head>
<body>
<main>
  <section class="hero">
    <div class="hero-art">
      <img src="{ASSET}" alt="A diver with a tablet and a torch, an anglerfish, and a
           hydrothermal vent" loading="eager">
    </div>
    <div class="hero-say">
      <p class="kicker">Pixel Patrol · deep sea</p>
      <h1>{_clock(seconds)} of footage<br>nobody had watched</h1>
      <p class="lede">Expedition video read end to end and asked two questions: what
         moved, and what was it. {processed:,} recordings from {ran} expeditions,
         every animal the detector found, and a position and a clock on each one that
         published them.</p>
      <div class="stats">
        {_stat(f"{processed:,}", f"of {listed:,} recordings" if listed else "recordings read")}
        {_stat(f"{animals:,}", "animals found") if animals
               else _stat(f"{seen:,}", "slices with an animal")}
        {_stat(f"{taxa}", "names given")}
        {_stat(f"{len([r for r in rows if r.report])}", "reports")}
      </div>
      {f'<a class="cta" href="{html.escape(combined, quote=True)}">Open all {len(everything)} expeditions together &rarr;</a>' if combined else ''}
    </div>
    <div class="hero-tiles" id="heroTiles" aria-hidden="true"></div>
    <a class="scroll-cue" href="#warning">what this is worth &darr;</a>
  </section>

  <section class="warning" id="warning">
    <h2>Read this before you believe any of it</h2>
    <div class="warn-grid">
      <div>
        <h3>A proof of concept</h3>
        <p>This is a demonstration that an archive of unwatched video can be read,
           indexed and made browsable. It is not a survey, and nothing here has been
           checked by anyone who knows these animals.</p>
      </div>
      <div>
        <h3>The names are guesses</h3>
        <p>Every name came from one object detector - a YOLOv5 checkpoint trained on
           MBARI imagery, 499 classes - run on frames it has never seen. It is
           confidently wrong often: on a set of labelled specimens it named two of
           five correctly and was wrong at 0.94 confidence on the others. Where an
           animal's looks disagreed, the name was resolved to the rank they share,
           or to <b>Undecided</b>. Read a name as a shortlist, not an
           identification.</p>
      </div>
      <div>
        <h3>Most frames were never read</h3>
        <p>Recordings are thinned before analysis and the detector looks about once a
           second, so most frames were never seen by anything. Of each expedition,
           only a sample of dives and a sample of each dive's bottom time was
           analysed at all - the counts below say how much.</p>
      </div>
      <div>
        <h3>Counts are upper bounds</h3>
        <p>One animal seen across several seconds is linked into one, but the linking
           is geometric and imperfect - measured against a tracking benchmark it
           splits the average animal across about 1.8 entries. The long tail is
           single, low-confidence detections. Filter by confidence before quoting a
           number.</p>
      </div>
      <div>
        <h3>The picture is a proxy</h3>
        <p>Almost all of this footage is published at 640×360, and some of the older
           tapes at 360×240. That costs the detector about six points of recall
           against full resolution, and no upscaling gets it back.</p>
      </div>
      <div>
        <h3>Colour is the vehicle's</h3>
        <p>Everything below the photic zone is lit by the lamps that filmed it, and
           water takes the red out within metres. Colour measurements here describe
           the lighting as much as the animal.</p>
      </div>
    </div>
  </section>

  <section class="explore" id="explore">
    <header class="explore-head">
      <h2>Everything that was found</h2>
      <p class="lede">Click a branch to go into it. The pictures are every animal the
         detector put under that name, most confident first, loaded as you scroll.
         Hover one to watch the seconds around it.</p>
      <nav class="jumps" id="jumps"></nav>
      <nav class="crumbs" id="crumbs"></nav>
    </header>
    <div class="explore-body">
      <div class="sun-side">
        <div class="sun"><svg id="sunburst" viewBox="-100 -100 200 200" role="img"
             aria-label="the taxonomy of what was found"></svg>
          <div class="sun-centre" id="sunCentre"></div>
        </div>
        <aside class="about" id="about"></aside>
      </div>
      <div class="wall" id="wall"><p class="empty">Loading what was found…</p></div>
    </div>
    <div class="sentinel" id="more"></div>
  </section>

  <section class="fleet">
    <h2>The expeditions</h2>
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
    <h2>Whose work this is</h2>
    <div class="credit-grid">
      <div>
        <h3>The footage</h3>
        <p>Every frame was filmed, published and paid for by somebody else. NOAA Ocean
           Exploration's Okeanos Explorer cruises and its earlier programmes are in the
           public domain; MBARI's DeepSea-MOT sequences are CC BY; the Axial Seamount
           camera belongs to the Ocean Observatories Initiative's Regional Cabled Array,
           run by the University of Washington. Each expedition below links to its own
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
           resolved once per class name. Every taxon on this page links back to its
           WoRMS record, which is where to check what the detector claimed.</p>
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
<script>{SCRIPT}</script>
</body></html>"""


def _stat(value: str, label: str) -> str:
    return f'<div class="stat"><b>{html.escape(value)}</b><span>{html.escape(label)}</span></div>'


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
  --abyss: #04101c; --abyss-2: #071a2b; --ink: #e8f4ff; --dim: #8aa6bd;
  /* The accent is the colour of the footage itself. Every metre of depth takes
     more of the red out, so a deep-sea frame is cyan whatever is in it - measured
     across this collection, the hue sits between 150 and 240 degrees. */
  --glow: #35d6f5; --warn: #ffb020;
  --line: rgba(140,190,220,.18); --card: rgba(255,255,255,.035);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
body { margin: 0; background: var(--abyss); color: var(--ink);
       font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
       -webkit-font-smoothing: antialiased; }
h1, h2, h3 { line-height: 1.15; letter-spacing: -.02em; margin: 0; }
a { color: var(--glow); }

/* ── the hero ─────────────────────────────────────────────────────────────── */
.hero { min-height: 100vh; display: grid; position: relative; overflow: hidden;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        align-items: center; gap: 3rem; padding: 5vh 6vw 12vh;
        background:
          radial-gradient(120% 90% at 15% 0%, #0d2d47 0%, transparent 60%),
          radial-gradient(80% 70% at 90% 20%, #06283d 0%, transparent 55%),
          linear-gradient(180deg, var(--abyss-2), var(--abyss) 70%); }
.hero-art { position: relative; justify-self: center; max-width: 42rem; }
/* Black line art on white, which is the wrong way round down here. */
.hero-art img { width: 100%; height: auto; filter: invert(1) brightness(1.08);
                mix-blend-mode: screen; opacity: .92; }
.hero-say { max-width: 34rem; }
.kicker { color: var(--glow); text-transform: uppercase; letter-spacing: .18em;
          font-size: .72rem; margin: 0 0 1rem; }
.hero h1 { font-size: clamp(2.2rem, 5vw, 3.6rem); }
.lede { color: var(--dim); max-width: 40rem; }
.hero .lede { margin: 1.1rem 0 2rem; font-size: 1.05rem; }
.stats { display: grid; grid-template-columns: repeat(2, minmax(8rem, 1fr));
         gap: .8rem; margin-bottom: 2rem; }
.stat { border: 1px solid var(--line); border-radius: 12px; padding: .7rem .9rem;
        background: var(--card); }
@media (min-width: 1500px) { .stats { grid-template-columns: repeat(4, 1fr); } }
.stat b { display: block; font-size: 1.45rem; letter-spacing: -.03em; }
.stat span { color: var(--dim); font-size: .8rem; }
.cta { display: inline-block; background: var(--glow); color: #032331; font-weight: 600;
       padding: .75rem 1.3rem; border-radius: 999px; text-decoration: none;
       box-shadow: 0 0 40px rgba(53,214,245,.25); }
.cta:hover { filter: brightness(1.1); }
.hero-tiles { position: absolute; inset: auto 0 0; height: 132px; display: flex;
              gap: 10px; padding: 0 6vw 2.2rem; pointer-events: none;
              mask-image: linear-gradient(90deg, transparent, #000 12%, #000 88%, transparent); }
.hero-tiles img { width: 96px; height: 96px; object-fit: cover; border-radius: 10px;
                  border: 1px solid var(--line); opacity: 0; animation: rise .9s forwards; }
@keyframes rise { from { opacity: 0; transform: translateY(14px); }
                  to { opacity: .85; transform: none; } }
.scroll-cue { position: absolute; left: 50%; bottom: 1rem; transform: translateX(-50%);
              color: var(--dim); font-size: .8rem; text-decoration: none; }

/* ── the warning ──────────────────────────────────────────────────────────── */
.warning { padding: 4rem 6vw; border-top: 1px solid var(--line);
           background: linear-gradient(180deg, rgba(255,176,32,.07), transparent 60%); }
.warning h2 { font-size: clamp(1.4rem, 3vw, 2rem); color: var(--warn); margin-bottom: 1.6rem; }
.warn-grid { display: grid; gap: 1.4rem 2.4rem;
             grid-template-columns: repeat(auto-fit, minmax(17rem, 1fr)); }
.warn-grid h3 { font-size: .95rem; margin-bottom: .3rem; }
.warn-grid p { color: var(--dim); margin: 0; font-size: .93rem; }

/* ── the taxonomy and its wall ────────────────────────────────────────────── */
.explore { padding: 4rem 6vw 2rem; border-top: 1px solid var(--line); }
.explore h2 { font-size: clamp(1.4rem, 3vw, 2rem); }
.explore-head .lede { margin: .7rem 0 1rem; }
.jumps { display: flex; flex-wrap: wrap; gap: .5rem; margin: 0 0 1rem; }
.jumps .jump { background: transparent; color: var(--dim); border: 1px solid var(--line);
               border-radius: 999px; padding: .3rem .85rem; font: inherit;
               font-size: .82rem; cursor: pointer; }
.jumps .jump span { color: var(--ink); margin-left: .35rem; }
.jumps .jump:hover { border-color: var(--glow); color: var(--ink); }
.jumps .jump.odd { border-color: #8a6a3a; color: #f0c274; }
.jumps .jump.odd span { color: #f0c274; }
.crumbs { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center;
          margin-bottom: 1.2rem; min-height: 1.8rem; }
.crumbs button { background: var(--card); color: var(--ink); border: 1px solid var(--line);
                 border-radius: 999px; padding: .2rem .7rem; font: inherit;
                 font-size: .82rem; cursor: pointer; }
.crumbs button:hover { border-color: var(--glow); }
.crumbs .sep { color: var(--dim); }
.crumbs .only { border-color: #4a5f72; color: var(--dim); }
.explore-body { display: grid; grid-template-columns: minmax(260px, 26rem) minmax(0, 1fr);
                gap: 2.5rem; align-items: start; }
.sun { position: relative; }
.sun svg { width: 100%; height: auto; display: block; }
.sun path { cursor: pointer; transition: opacity .15s; }
.sun path:hover { opacity: .75; }
.sun-centre.hovering span { color: var(--ink); }
.sun-centre { position: absolute; inset: 38% 34% auto; text-align: center;
              pointer-events: none; }
.sun-centre b { display: block; font-size: 1rem; }
.sun-centre span { color: var(--dim); font-size: .74rem; display: block;
                  line-height: 1.15; overflow-wrap: anywhere; }
.wall { display: grid; gap: 8px; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); }
.wall .empty { color: var(--dim); grid-column: 1 / -1; }
.tile { position: relative; margin: 0; border-radius: 10px; overflow: hidden;
        border: 1px solid var(--line); background: #00121f; aspect-ratio: 1;
        cursor: pointer; align-self: start; }
/* The crops are whatever size the animal was on screen. Fill the tile from the
   column width - stretched in both axes the aspect ratio is ignored and a row of
   small crops collapses. */
.tile img { position: absolute; inset: 0; width: 100%; height: 100%;
            object-fit: cover; display: block; }
.tile figcaption { position: absolute; inset: auto 0 0; padding: .3rem .4rem;
                   font-size: .68rem; line-height: 1.25; color: #cfe9f7;
                   background: linear-gradient(transparent, rgba(0,10,20,.92));
                   opacity: 0; transition: opacity .15s; }
.tile:hover figcaption { opacity: 1; }
.tile .conf { color: var(--glow); }
.tile.moving::after { content: "▶"; position: absolute; top: 4px; right: 5px;
                      font-size: .6rem; color: var(--glow); opacity: .8; }
.sentinel { height: 4rem; }
.sun-side { position: sticky; top: 1.5rem; }
.about { margin-top: 1.2rem; border: 1px solid var(--line); border-radius: 12px;
         padding: .9rem 1rem; background: var(--card); }
.about h3 { font-size: 1.05rem; margin-bottom: .2rem; }
.about .rank { color: var(--dim); font-size: .72rem; text-transform: uppercase;
               letter-spacing: .08em; }
.about .count { color: var(--glow); margin: 0 0 .4rem; font-size: .88rem; }
.about .lineage { color: var(--dim); font-size: .8rem; margin: 0 0 .6rem; }
.about .hint { color: var(--dim); font-size: .85rem; margin: 0; }
.worms { font-size: .85rem; text-decoration: none; }
.worms:hover { text-decoration: underline; }

/* ── credit ───────────────────────────────────────────────────────────────── */
.credits { padding: 3.5rem 6vw; border-top: 1px solid var(--line);
           background: linear-gradient(180deg, rgba(53,214,245,.05), transparent 55%); }
.credits h2 { font-size: clamp(1.3rem, 2.6vw, 1.7rem); margin-bottom: 1.4rem; }
.credit-grid { display: grid; gap: 1.4rem 2.4rem;
               grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); }
.credit-grid h3 { font-size: .92rem; margin-bottom: .3rem; color: var(--glow); }
.credit-grid p { color: var(--dim); font-size: .9rem; margin: 0; }
a.mission { margin-right: .35rem; }

/* ── the fleet ────────────────────────────────────────────────────────────── */
.fleet { padding: 3rem 6vw 4rem; border-top: 1px solid var(--line); }
.fleet h2 { font-size: clamp(1.4rem, 3vw, 2rem); margin-bottom: 1.2rem; }
.fleet-table { width: 100%; border-collapse: collapse; font-size: .92rem; }
.fleet-table th { text-align: left; color: var(--dim); font-weight: 500;
                  font-size: .78rem; text-transform: uppercase; letter-spacing: .08em;
                  border-bottom: 1px solid var(--line); padding: .5rem .6rem; }
.fleet-table td { border-bottom: 1px solid var(--line); padding: .6rem; vertical-align: top; }
.fleet-table .num { white-space: nowrap; }
.fleet-table .sub { display: block; color: var(--dim); font-size: .76rem; }
.fleet-table a { text-decoration: none; border: 1px solid var(--line); border-radius: 999px;
                 padding: .25rem .7rem; font-size: .82rem; white-space: nowrap; }
.fleet-table a:hover { border-color: var(--glow); }
.note { color: var(--dim); font-size: .86rem; margin-top: 1rem; }
footer { padding: 2rem 6vw 4rem; color: var(--dim); font-size: .85rem;
         border-top: 1px solid var(--line); }
code { background: var(--card); padding: .1rem .35rem; border-radius: 4px; }

@media (max-width: 900px) {
  .hero { grid-template-columns: 1fr; padding-bottom: 16vh; }
  .hero-art { max-width: 26rem; }
  .explore-body { grid-template-columns: 1fr; }
  .sun { position: static; max-width: 24rem; margin: 0 auto; }
}
"""


SCRIPT = r"""
/* The page is a browser over the store `tiles` wrote: an index of the taxonomy,
   and beside it a directory per taxon holding pages of animals. Nothing is built
   into the HTML, so the page does not grow with the collection - what is on screen
   is what has been fetched. */
const TILES = 'tiles';
const state = { index: null, path: [], taxa: [], queue: [], loading: false,
                done: false, pages: new Map(), clips: new Map() };

const el = (id) => document.getElementById(id);

/* Names that are not taxa and would otherwise send a reader to a register that has
   never heard of them. */
const ASIDES = {
  Undecided: `The detector saw this animal across several seconds and called it
    something different each time, and the names disagreed too far up to share a
    rank - a fish one second, a sponge the next. Rather than pick the loudest guess
    the name was dropped. These are worth a look: they are where the detector is
    least sure, and where something unusual is most likely to be hiding.`,
  Unplaced: `Names the World Register of Marine Species does not carry - the
    detector's own classes for gear, marks, substrate and animals it names its own
    way, plus the ones whose frames disagreed.`,
};
const say = (n) => n.toLocaleString();

async function boot() {
  try {
    state.index = await (await fetch(`${TILES}/index.json`)).json();
  } catch (err) {
    el('wall').innerHTML = '<p class="empty">No pictures were written beside this page. '
      + 'Run <code>collect site</code> where the reports are.</p>';
    return;
  }
  focusOn(...fromHash());
  drawJumps();
  heroTiles();
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

/** The slice of a ring for animals a branch never resolved past its own name. */
function restArc(node, from, to, depth) {
  const kids = node.children || [];
  if (!kids.length) return;
  const rest = node.count - kids.reduce((sum, child) => sum + child.count, 0);
  if (rest <= 0) return;
  const inner = 16 + depth * 26, outer = inner + 24;
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', arc(from, to, inner, outer));
  path.setAttribute('class', 'rest');
  path.setAttribute('fill', '#4a5f72');
  path.setAttribute('fill-opacity', String(0.5 - depth * 0.08));
  path.setAttribute('stroke', '#04101c');
  path.setAttribute('stroke-width', '0.6');
  const label = { name: `${node.name}, no finer name`, count: rest };
  const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
  title.textContent = `${label.name} — ${say(rest)}`;
  path.appendChild(title);
  path.addEventListener('mouseenter', () => nameInHole(label));
  path.addEventListener('mouseleave', () => nameInHole(null));
  path.addEventListener('click', () => focusOn(pathOf(node), node.name));
  el('sunburst').appendChild(path);
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

/** Straight ways in: the big branches, and the animals that could not be named.
 *
 * Undecided is not a failure to be hidden - it is every animal the detector called
 * one thing and then another, which is where its confusion is legible. */
function drawJumps() {
  const jumps = el('jumps');
  if (!jumps) return;
  const root = state.index.tree;
  const branches = [];
  const walk = (node, path) => {
    for (const child of node.children || []) {
      branches.push({ node: child, path: [...path, child.name] });
      walk(child, [...path, child.name]);
    }
  };
  walk(root, []);
  const biggest = branches
    .filter(b => (b.node.children || []).length)
    .sort((a, b) => b.node.count - a.node.count)
    .slice(0, 4);
  const odd = branches.filter(b => b.node.name === 'Undecided');
  for (const { node, path } of [...odd, ...biggest]) {
    const button = document.createElement('button');
    button.className = node.name === 'Undecided' ? 'jump odd' : 'jump';
    button.innerHTML = `${escape(node.name)} <span>${say(node.count)}</span>`;
    button.addEventListener('click', () => {
      focusOn(path);
      el('explore').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    jumps.appendChild(button);
  }
}

/** The path from the root down to a node, for a click on any ring. */
function pathOf(node) {
  const from = state.basePath || [];
  const base = nodeAt(from);
  if (node === base) return from;
  return [...from, ...(pathTo(base, node) || [])];
}

/** Three rings out from whatever is in focus, each child an arc of its parent. */
function drawSun() {
  const svg = el('sunburst');
  const focus = nodeAt(state.path);
  // A leaf has no rings of its own. Draw its parent's and light the leaf up, so
  // the reader keeps the context instead of staring at an empty circle.
  const leaf = !(focus.children || []).length && state.path.length > 0;
  state.basePath = leaf ? state.path.slice(0, -1) : state.path;
  const base = nodeAt(state.basePath);
  svg.innerHTML = '';
  const rings = 3, width = 26, hole = 16;
  let reach = hole + width * 2;  // how far out anything got drawn, floored at two rings
  const place = (node, from, to, depth) => {
    if (depth >= rings || to - from < 0.012) return;
    for (const child of node.children || []) {
      const span = (to - from) * (child.count / Math.max(1, node.count));
      const inner = hole + depth * width, outer = inner + width - 2;
      reach = Math.max(reach, outer);
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', arc(from, from + span, inner, outer));
      path.setAttribute('fill', colourFor(child.name, depth));
      path.setAttribute('fill-opacity', String(0.92 - depth * 0.18));
      const here = leaf && child.name === focus.name;
      path.setAttribute('stroke', here ? '#7fd4ff' : '#04101c');
      path.setAttribute('stroke-width', here ? '1.6' : '0.6');
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      title.textContent = `${child.name} — ${say(child.count)}`;
      path.appendChild(title);
      path.addEventListener('click', () => focusOn(pathOf(child)));
      path.addEventListener('mouseenter', () => nameInHole(child));
      path.addEventListener('mouseleave', () => nameInHole(null));
      svg.appendChild(path);
      place(child, from, from + span, depth + 1);
      from += span;
    }
    // Whatever angle is left over belongs to animals named at this rank and no
    // deeper - called Porifera and nothing more. Without an arc that reads as a
    // hole in the ring, and it is often the commonest answer the detector gives.
    if (to - from > 0.012 && depth < rings) restArc(node, from, to, depth);
  };
  place(base, 0, Math.PI * 2, 0);
  // A branch two ranks deep should not sit in a circle drawn for five.
  svg.setAttribute('viewBox', `${-reach - 4} ${-reach - 4} ${2 * reach + 8} ${2 * reach + 8}`);
  nameInHole(null);
  drawAbout(focus);
}

/** The hole in the middle reads whatever the pointer is over, or the focus itself. */
function nameInHole(node) {
  const focus = nodeAt(state.path);
  const shown = node || focus;
  el('sunCentre').innerHTML = `<b>${say(shown.count)}</b><span>${shown.name === 'everything'
    ? 'animals' : escape(shown.name)}</span>`;
  el('sunCentre').classList.toggle('hovering', Boolean(node));
}

/** What this branch is, for a reader who has never heard of it.
 *
 * The register's own record, not a search: someone meeting `Asbestopluma` for the
 * first time should land on the people whose business it is. A name the register
 * does not know says so - which is information, since it usually means the
 * detector's class is not a taxon at all. */
function drawAbout(focus) {
  const about = el('about');
  if (focus.name === 'everything') {
    about.innerHTML = `<p class="hint">Every animal the detector found, arranged by what
      it called them. Click a ring to go in; the pictures beside it follow.</p>`;
    return;
  }
  const aside = ASIDES[focus.name];
  if (aside) {
    about.innerHTML = `<h3>${escape(focus.name)}</h3>
      <p class="count">${say(focus.count)} animals</p><p class="hint">${aside}</p>`;
    return;
  }
  const named = state.index.taxa[focus.name];
  const under = taxaUnder(focus).length;
  const lineage = (named && named.above) || [];
  const worms = named && named.aphia
    ? `<a class="worms" href="https://www.marinespecies.org/aphia.php?p=taxdetails&id=${named.aphia}"
         target="_blank" rel="noopener">Look it up in WoRMS &rarr;</a>`
    : `<p class="hint">The World Register of Marine Species has no record under this
        name, which usually means the detector's class is not a taxon - gear, a
        substrate, or a name it made its own way.</p>`;
  about.innerHTML = `
    <h3>${escape(focus.name)}${named && named.rank ? ` <span class="rank">${escape(named.rank)}</span>` : ''}</h3>
    <p class="count">${say(focus.count)} animals${under > 1 ? ` across ${under} names` : ''}</p>
    ${lineage.length ? `<p class="lineage">${lineage.map(escape).join(' › ')}</p>` : ''}
    ${worms}`;
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
  el('wall').innerHTML = '';
  drawSun();
  drawCrumbs();
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
      wall.appendChild(tileFor(animal, still, slug, next.page));
    }
  } catch (err) {
    /* a page that will not load is one page, not the end of the wall */
  } finally {
    state.loading = false;
  }
  // Keep filling until the sentinel is off screen, or a tall display never scrolls.
  if (el('more').getBoundingClientRect().top < window.innerHeight * 1.5) loadMore();
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

function tileFor(animal, still, slug, page) {
  const figure = document.createElement('figure');
  figure.className = 'tile' + (animal.m ? ' moving' : '');
  const img = document.createElement('img');
  img.loading = 'lazy';
  img.decoding = 'async';
  img.alt = animal.t;
  img.src = asPicture(still);
  figure.appendChild(img);
  const caption = document.createElement('figcaption');
  caption.innerHTML = `${escape(animal.t)}<br><span class="conf">${animal.c.toFixed(2)}</span>`
    + ` · ${escape(animal.e)} · ${clock(animal.s)}`
    + (animal.a < 1 ? ` · agreed ${Math.round(animal.a * 100)}%` : '');
  figure.appendChild(caption);
  if (animal.m) animate(figure, img, slug, page, animal);
  return figure;
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

/* ── the hero's drifting tiles ────────────────────────────────────────────── */

async function heroTiles() {
  const strip = el('heroTiles');
  const names = Object.keys(state.index.taxa)
    .sort((a, b) => state.index.taxa[b].count - state.index.taxa[a].count)
    .slice(0, 14);
  let shown = 0;
  for (const name of names) {
    if (shown >= 9) break;
    try {
      const { animals, stills } = await pageOf(state.index.taxa[name].slug, 0);
      const wanted = Math.floor(Math.random() * Math.min(6, animals.length));
      const animal = animals[wanted];
      if (!animal) continue;
      let at = 0;
      for (const before of animals.slice(0, wanted)) at += before.l;
      const img = document.createElement('img');
      img.src = asPicture(stills.slice(at, at + animal.l));
      img.alt = animal.t;
      img.title = `${animal.t} — ${animal.c.toFixed(2)}`;
      img.style.animationDelay = `${shown * 90}ms`;
      strip.appendChild(img);
      shown++;
    } catch { /* one tile fewer is not worth a message */ }
  }
}

if (window.IntersectionObserver) {
  new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting)) loadMore();
  }, { rootMargin: '600px' }).observe(el('more'));
}
boot();
"""
