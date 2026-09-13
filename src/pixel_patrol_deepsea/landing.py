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

Five numbered parts, and a warning that is not one of them:

    01 the collection   what this is and where the footage comes from, in three
                        sentences, over the figures that say how much of it there
                        is. Short: the pictures are the point of the page.
       the disclaimer   what the figures are worth: one line and six short ones,
                        all of them visible. Behind a summary they were read by
                        nobody, which is the same as not writing them.
    02 what is in it    a half sunburst against the left edge and, beside it, every
                        picture of whatever branch is in focus. Grouped by phylum,
                        which is the rank the reports group by. The pictures scroll
                        in their own box: a wall that grew the page put everything
                        below it out of reach.
    03 the expeditions  one line each: how much was analysed, the way into its
                        report, and - above them all - the report that spans them.
    04 whose work this is
    05 what you kept    the sightings somebody starred, and the CSV of them.

Clicking a picture opens the second of footage it was cut from: the archives serve
their own files over byte ranges, so the page seeks into a recording without
copying a frame of it, and draws the detector's own box over the moment. That is
the only honest way to show what a detection was - a crop of a 640-pixel frame
proves very little, and the seconds around it are the evidence.

Nothing below the header is built into the HTML - it is read from `tiles/index.json`
and the paged files beside it, which is why the page is the same size whether the
collection holds one expedition or fifty. What somebody keeps lives in their own
browser and is sent nowhere; the CSV is how they take it with them.

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
# Every recording that has been read, in the order it was filmed, one stripe each.
# Written by `banner.py` from the colours the processors measured.
BANNER = "assets/colours.png"


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
    <div class="hero-say">
      <p class="chapter">01 / the collection</p>
      <h1>Somebody has to<br>watch the tapes.</h1>
      <p class="lede">NOAA, MBARI and the cabled observatories publish their dive
         footage in full - more hours of it than anybody is going to sit through.
         This reads it a frame a second and asks two questions: what moved, and
         what was it.</p>
    </div>
    <aside class="alarm" id="warning">
      <p class="alarm-line"><b>Prototype</b> An experiment in pulling statistics and
         animal names out of footage nobody has time to watch. Nothing on this page
         has been checked by a person.</p>
      <ul>
        <li>Every name is one detector's guess: a shortlist, not an identification.</li>
        <li>Most frames were never read - a sample of dives, a look a second.</li>
        <li>Counts are upper bounds; one animal is often counted twice.</li>
        <li>640×360 footage costs the detector about six points of recall.</li>
        <li>Colour is the vehicle's lamps as much as the animal.</li>
      </ul>
    </aside>
    <p class="banner-says">every recording that has been read, one stripe each, in
       the colours it was filmed in - oldest on the left</p>
  </section>

  <div class="readout">
    {_cell(_clock(seconds), "footage read")}
    {_cell(f"{processed:,} / {listed:,}" if listed else f"{processed:,}", "recordings")}
    {_cell(f"{animals:,}", "animals") if animals
           else _cell(f"{seen:,}", "slices with an animal")}
    {_cell(f"{taxa}", "names given")}
  </div>

  <section class="explore" id="explore">
    <header class="explore-head">
      <p class="chapter">02 / what is in it</p>
      <h2>Everything that was found</h2>
      <p class="lede">Named by one object detector - a FathomNet YOLOv5 checkpoint,
         499 classes, on a frame a second - and grouped by the ranks the World
         Register of Marine Species puts above those names. Click a ring to go in
         and the middle of it to come out; hover a picture to watch it move, or
         click it to open the recording there.</p>
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

  <section class="kept" id="kept" hidden>
    <p class="chapter">what you kept</p>
    <h2>Your favourites</h2>
    <p class="lede">Starred sightings, kept in this browser and sent nowhere. The CSV
       carries the name, the recording, the second and the box the detector drew,
       which is enough for somebody else to find the moment in the archive's own
       file.</p>
    <p class="kept-does">
      <a class="cta" id="keptCsv" download="deepsea-favourites.csv" href="#"
         >download the csv &darr;</a>
      <button class="quiet" id="keptClear">forget all of them</button>
    </p>
    <div class="wall kept-wall" id="keptWall"></div>
  </section>

  <section class="fleet">
    <p class="chapter">03 / the expeditions</p>
    <h2>Where the footage came from</h2>
    {_together(combined)}
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
        <p>Every frame was filmed, published and paid for by somebody else, and the
           three archives here do not ask for the same thing. NOAA Ocean Exploration's
           video is in the public domain and asks to be credited to
           <b>NOAA Ocean Exploration</b>. MBARI's DeepSea-MOT is
           <b>CC BY-SA 4.0</b>, so the crops taken from it carry that licence too, and
           it is cited as Barnard et al. 2025, <i>DeepSea MOT</i>, arXiv:2509.03499.
           The Axial Seamount camera is the Ocean Observatories Initiative's Regional
           Cabled Array, run by the University of Washington: open with no licence
           named, and its terms require acknowledging the <b>National Science
           Foundation</b> and the <b>WHOI OOI Program Office</b>. Every picture on this
           page says which of the three it is.</p>
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
        <p><b>Every name on this page is an automated guess</b> - one object
           detector's answer for one frame, not an identification, and wrong often
           enough that the page says so wherever a name appears. Ranks and
           identifiers come from the
           <a href="https://www.marinespecies.org/">World Register of Marine Species</a>
           (text CC BY; WoRMS Editorial Board, 2026, doi:10.14284/170), resolved once
           per class name; article titles from
           <a href="https://en.wikipedia.org/">Wikipedia</a> (CC BY-SA), and a name is
           linked there only where it actually has an article. Nothing on this page
           describes an animal in its own words.</p>
      </div>
      <div class="credit-tool">
        <h3>This tool</h3>
        <p><a href="https://github.com/ida-mdc/pixel-patrol">Pixel Patrol</a> reads
           image and video collections and reports what is in them; this is its
           deep-sea extension. That is Pixel Patrol herself, gone diving.</p>
        <img class="patrol" src="{ASSET}" alt="Pixel Patrol with a tablet and a
             torch, an anglerfish, and a hydrothermal vent" loading="lazy">
      </div>
    </div>
  </section>

  <section class="imprint">
    <p class="chapter">imprint</p>
    <div class="imprint-grid">
      <div>
        <h3>Responsible (§5 DDG)</h3>
        <p>Deborah Schmidt<br>
           Helmholtz Imaging / Max-Delbrück-Centrum für Molekulare Medizin in der
           Helmholtz-Gemeinschaft (MDC)<br>
           Robert-Rössle-Straße 10, 13125 Berlin, Germany<br>
           <a href="mailto:deborah.schmidt@mdc-berlin.de"
             >deborah.schmidt@mdc-berlin.de</a></p>
      </div>
      <div>
        <h3>Liability</h3>
        <p>Pixel Patrol is provided free of charge. The MDC does not guarantee the
           accuracy of the metrics or of the graphical representation of the data
           processed and is not liable for any errors of the software. Use of the
           service and any subsequent use of the generated representations is at
           your own risk. By using the service, you agree to indemnify and hold the
           MDC harmless from and against any third-party claims that may arise as a
           result of your use of it.</p>
      </div>
      <div>
        <h3>The data is not ours</h3>
        <p>No recording is hosted here: each one plays from the archive that
           published it. The crops and the few seconds of film beside them are
           derived from that footage and are redistributed under its terms - public
           domain for NOAA Ocean Exploration, CC BY-SA 4.0 for MBARI's DeepSea-MOT
           (so these derivatives are CC BY-SA 4.0 as well), and open with a required
           acknowledgement of the NSF and the WHOI OOI Program Office for the Ocean
           Observatories Initiative's Regional Cabled Array at the University of
           Washington. Cite the archive, not this page.</p>
      </div>
      <div>
        <h3>What was made here</h3>
        <p>The detections, the names and the figures - from
           <a href="https://fathomnet.org/">FathomNet</a>'s YOLOv5 weights (CC BY
           4.0), the World Register of Marine Species (CC BY) and Wikipedia's
           article titles (CC BY-SA). They are a machine's reading of somebody
           else's footage, offered as a prototype and not as a survey.</p>
      </div>
    </div>
  </section>

  <footer>
    Written by <code>python -m pixel_patrol_deepsea.collect site</code> on
    {datetime.now().strftime('%Y-%m-%d %H:%M')}. Serve this folder over HTTP and every
    link works; there is nothing else to install.
  </footer>
</main>

<div class="stage" id="stage" hidden>
  <div class="stage-box" role="dialog" aria-modal="true"
       aria-label="the moment this animal was found">
    <p class="stage-head">
      <img class="stage-crop" id="stageCrop" alt="">
      <span class="stage-said"><b id="stageName"></b>
        <span id="stageFacts"></span><br><span id="stageWhere"></span></span>
      <button class="star" id="stageStar" title="keep this sighting">&#9734;</button>
      <button class="shut" id="stageShut" title="close (esc)">&#10005;</button>
    </p>
    <div class="stage-play" id="stagePlay"></div>
    <p class="stage-foot">
      <button class="quiet" id="stageBack">&#8630; back to the moment</button>
      <label><input type="checkbox" id="stageBox" checked> the boxes, as the
        detector drew them</label>
      <a id="stageFile" target="_blank" rel="noopener">the recording itself &nearr;</a>
      <span class="stage-note" id="stageNote"></span>
    </p>
    <p class="stage-terms" id="stageTerms"></p>
  </div>
</div>
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

    It sits with the expeditions rather than in the header, because it is one of
    them - the row above all the rows, and a reader reaches for it after seeing what
    the others are. It is not another gallery, and saying "open all nine expeditions"
    did not warn anybody of that: it is the statistics over every expedition that has
    been read, and it carries no pictures at all, because those are hundreds of
    megabytes and belong in an expedition's own report. It opens grouped by
    expedition, since the only reason to put them in one report is to compare them.
    """
    if not url:
        return ""
    return (f'<p class="together"><a class="cta" href="{html.escape(url, quote=True)}">'
            f'open all of them in one report &rarr;</a>'
            f'<span>Counts, names, depths, positions and times, grouped by '
            f'expedition. No pictures: each expedition\'s own report holds those.'
            f'</span></p>')


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


def _named(row) -> str:
    """The expedition's name, linked to its own page where it has one.

    The link used to sit at the far end of the row, next to the report, competing
    with it. An expedition's name is the obvious thing to click to read about the
    expedition, and it leaves the end of the row to the report.
    """
    title = html.escape(row.title)
    link = getattr(row, "link", "")
    if not link:
        return f"<b>{title}</b>"
    return (f'<b><a class="mission" href="{html.escape(link, quote=True)}" '
            f'target="_blank" rel="noopener" title="what the ship was doing"'
            f'>{title} &nearr;</a></b>')


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
      <td>{_named(row)}<span class="sub">{html.escape(row.id)}</span></td>
      <td class="num">{html.escape(row.date or '—')}</td>
      <td class="num">{read}</td>
      <td class="num">{_clock(row.seconds)}</td>
      <td class="num">{animals}</td>
      <td class="num">{taxa}</td>
      <td class="num">{_link(row)}</td>
    </tr>"""


STYLE = """
:root {
  --abyss: #04101c; --abyss-2: #071a2b; --ink: #e8f4ff; --dim: #89a5bc;
  /* The accent is the colour of the footage itself. Every metre of depth takes
     more of the red out, so a deep-sea frame is cyan whatever is in it - measured
     across this collection, the hue sits between 150 and 240 degrees. */
  --glow: #35d6f5; --warn: #ffb020;
  /* The one thing on the page that is not the footage's own colour, which is the
     point of it: a report to open is not a taxon to click, and cyan on cyan made
     the two the same thing. */
  --coral: #ff7a6a;
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
/* The collection's own colours behind the words: one stripe per recording, in the
   order they were filmed, scrimmed hard enough to read over. It is the data and not
   a texture - the near-black columns are transits and night, and the warm ones are
   a vehicle's own hardware in front of its lamps. */
.hero { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
        align-items: center; gap: 3rem; padding: 3.2rem 6vw 3.4rem;
        position: relative; overflow: hidden;
        background-image:
          linear-gradient(100deg, rgba(4,16,28,.95) 0%, rgba(4,16,28,.8) 32%,
                          rgba(4,16,28,.5) 62%, rgba(4,16,28,.34) 100%),
          linear-gradient(180deg, rgba(4,16,28,.3), rgba(4,16,28,.05) 45%,
                          rgba(4,16,28,.55)),
          url("assets/colours.png"); background-size: cover;
        background-position: center; }
.hero-say { max-width: 38rem; }
.hero h1 { font-size: clamp(1.9rem, 3.4vw, 2.7rem); }
.lede { color: var(--dim); max-width: 34rem; }
.hero .lede { margin: 1.2rem 0 0; color: #b9d2e4; }
.banner-says { position: absolute; left: 6vw; bottom: .6rem; margin: 0;
               max-width: 30rem; color: rgba(180,205,225,.7);
               font: .62rem/1.4 var(--mono); letter-spacing: .06em; }
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
/* The warning stands in the header beside the title, where a reader meets it before
   anything it is a warning about - and where, on a desktop, there was space doing
   nothing. */
.alarm { padding: .85rem 1.1rem .95rem; color: #ffe9c9; justify-self: end;
         max-width: 32rem; border: 1px solid rgba(255,176,32,.4);
         border-left: 3px solid var(--warn); background: rgba(24,16,6,.82); }
.alarm-line { margin: 0; font-size: .92rem; }
.alarm b { color: var(--warn); font: .68rem/1 var(--mono); letter-spacing: .18em;
           text-transform: uppercase; margin-right: .7rem; }
/* Behind a summary, the six were behind a summary: read by nobody. They are one
   line each now and they are simply there. */
.alarm ul { margin: .6rem 0 0; padding: 0; list-style: none;
            display: grid; gap: .25rem; font-size: .82rem;
            color: rgba(255,233,201,.78); }
.alarm li { padding-left: 1rem; position: relative; }
.alarm li::before { content: "—"; position: absolute; left: 0; color: var(--warn);
                    opacity: .7; }

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
/* One size, one line, one height. Two sizes and a wrapping name meant the ring
   below jumped up and down as the pointer moved across it. */
.sun-read { margin: 0 0 .5rem; height: 1.8rem; display: flex; gap: .55rem;
            align-items: baseline; white-space: nowrap; overflow: hidden;
            font: .95rem/1.8rem var(--mono); }
.sun-read b { font-weight: 600; font-variant-numeric: tabular-nums; }
.sun-read span { color: var(--dim); overflow: hidden; text-overflow: ellipsis; }
.sun-read.hovering span { color: var(--ink); }

/* The pictures scroll in their own box. A wall that grows without end grows the
   page without end: everything below it was unreachable once a few thousand animals
   had loaded, and scrolling anywhere near the ring fetched more of them. */
.wall { max-height: min(88vh, 56rem); overflow-y: auto; overscroll-behavior: contain;
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
/* A tile is a way in, not a picture: clicking it opens the second of footage it was
   cut from. The star keeps it, and a kept star stays lit when the pointer leaves. */
.tile .star { position: absolute; right: 0; bottom: 0; opacity: 0; border: 0;
              background: none; color: #cfe9f7; cursor: pointer; padding: .2rem .35rem;
              font-size: .9rem; line-height: 1; transition: opacity .12s; }
.tile .star:hover { color: var(--glow); }
.tile .star.on { color: var(--warn); }
.tile:hover .star, .tile:focus-within .star, .tile .star.on { opacity: 1; }
.tile figcaption { padding-right: 1.4rem; }

/* ── the stage ───────────────────────────────────────────────────────────── */
/* A 100-pixel crop is not evidence of anything. The seconds around it are, and the
   archive serves its own file over byte ranges, so the page can open one at the
   second the animal was found without copying a frame of it. */
.stage { position: fixed; inset: 0; z-index: 20; display: grid; place-items: center;
         background: rgba(2,8,14,.86); padding: 4vh 4vw;
         backdrop-filter: blur(2px); }
.stage[hidden] { display: none; }
.stage-box { width: min(72rem, 100%); max-height: 92vh; overflow: auto;
             background: var(--abyss-2); border: 1px solid var(--line); }
.stage-head { display: flex; align-items: center; gap: .7rem; margin: 0;
              padding: .55rem .9rem; border-bottom: 1px solid var(--line); }
/* The crop that was clicked, kept in the corner: at a metre of seabed a box is easy
   to lose, and this is what the reader came in holding. */
.stage-crop { width: 2.4rem; height: 2.4rem; object-fit: cover; background: #00121f;
              flex: none; }
.stage-crop[hidden] { display: none; }
.stage-head b { font-size: 1rem; margin-right: .5rem; }
.stage-head .stage-said { display: block; min-width: 0; }
.stage-head span { color: var(--dim); font: .72rem var(--mono); letter-spacing: .04em;
                   font-variant-numeric: tabular-nums; }
#stageWhere { display: block; margin-top: .2rem; color: #9fc2da; }
#stageWhere .sep { color: #44596b; margin: 0 .1rem; }
#stageWhere .reel { color: var(--dim); }
.stage-head .star, .stage-head .shut { margin-left: auto; background: none; border: 0;
              color: var(--dim); cursor: pointer; font-size: 1.1rem; line-height: 1;
              padding: 0 .2rem; }
.stage-head .shut { margin-left: .2rem; }
.stage-head .star.on { color: var(--warn); }
.stage-head .star:hover, .stage-head .shut:hover { color: var(--ink); }
.stage-play { position: relative; background: #000; aspect-ratio: 16 / 9; }
.stage-play video, .stage-play img { position: absolute; inset: 0; width: 100%;
              height: 100%; object-fit: contain; display: block; background: #000; }
.stage-play svg { position: absolute; inset: 0; width: 100%; height: 100%;
                  pointer-events: none; }
/* Every animal the detector found in this recording, drawn where it is now. The
   one in focus is the bright one; the rest are there to be clicked. */
.stage-play svg { pointer-events: none; }
.stage-play svg rect { fill: none; stroke: rgba(140,190,220,.55); stroke-width: 1.5;
                       vector-effect: non-scaling-stroke; cursor: pointer;
                       pointer-events: all; }
.stage-play svg rect:hover { stroke: var(--ink); }
.stage-play svg rect.here { stroke: var(--glow); stroke-width: 2.5; }
.stage-play svg text { fill: rgba(200,225,240,.85); font-family: var(--mono);
                       font-size: 9px; paint-order: stroke; stroke: rgba(0,8,16,.85);
                       stroke-width: 3px; pointer-events: none; }
.stage-play svg text.here { fill: var(--glow); }
.stage-play.no-box svg { display: none; }
.stage-foot { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem 1.2rem;
              margin: 0; padding: .7rem .9rem; color: var(--dim);
              font: .72rem var(--mono); letter-spacing: .04em;
              border-top: 1px solid var(--line); }
.stage-foot label { display: flex; align-items: center; gap: .4rem; cursor: pointer; }
.stage-foot .quiet { padding: .35rem .6rem; letter-spacing: .08em; }
.stage-foot a { text-decoration: none; }
.stage-note { color: var(--warn); }
/* Whose footage this is, and what the name on it is worth. Under the picture
   rather than in a page-long disclaimer somewhere above it. */
.stage-terms { margin: 0; padding: .5rem .9rem .6rem; border-top: 1px solid var(--line);
               color: var(--dim); font: .68rem/1.5 var(--mono); letter-spacing: .04em; }
.stage-terms .credit { color: #9fc2da; }
.stage-terms .guess { color: var(--warn); }
.stage-terms .sep { color: #44596b; margin: 0 .15rem; }

/* ── 05 what somebody kept ────────────────────────────────────────────────── */
.kept { padding: 3rem 6vw; border-top: 1px solid var(--line); }
.kept h2 { font-size: clamp(1.3rem, 2.6vw, 1.9rem); }
.kept .lede { margin: .7rem 0 1.1rem; }
.kept-does { display: flex; flex-wrap: wrap; align-items: center; gap: 1rem;
             margin: 0 0 1.2rem; }
.kept-wall { max-height: 30rem; }
.quiet { background: none; border: 1px solid var(--line); border-radius: 2px;
         color: var(--dim); cursor: pointer; padding: .5rem .8rem;
         font: .7rem var(--mono); letter-spacing: .12em; text-transform: uppercase; }
.quiet:hover { color: var(--ink); border-color: var(--dim); }
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
/* The report is the point of the row. The expedition's own page is a courtesy
   beside it - it was the louder of the two, which had it backwards. */
.fleet-table a.open { text-decoration: none; border: 1px solid var(--coral);
                      border-radius: 2px; padding: .34rem .7rem; font-size: .72rem;
                      white-space: nowrap; background: rgba(255,122,106,.14);
                      letter-spacing: .06em; color: var(--coral); }
.fleet-table a.open:hover { background: rgba(255,122,106,.28); }
.fleet-table a.mission { color: var(--ink); text-decoration: none; }
.fleet-table a.mission:hover { color: var(--coral); }
.together { display: flex; flex-wrap: wrap; align-items: center; gap: .9rem;
            margin: 0 0 1.6rem; }
.together .cta { padding: .8rem 1.1rem; color: var(--coral);
                 border-color: var(--coral); background: rgba(255,122,106,.1); }
.together .cta:hover { background: rgba(255,122,106,.22); }
.together span { color: var(--dim); font-size: .84rem; max-width: 30rem; }
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
/* Black line art on white, which is the wrong way round down here. Inverted, the
   paper is very nearly black but not quite - and `screen` lifted that last level or
   two, which on a page this dark is a visible rectangle around the drawing. The
   contrast step clips the paper to exactly black, where screening it is a no-op. */
.patrol { display: block; width: 100%; max-width: 15rem; margin-top: .8rem;
          filter: invert(1) contrast(1.2) brightness(1.04);
          mix-blend-mode: screen; opacity: .92; }
.imprint { padding: 2.5rem 6vw 3rem; border-top: 1px solid var(--line); }
.imprint-grid { display: grid; gap: 1.4rem 2.4rem;
                grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); }
.imprint-grid h3 { font-size: .72rem; margin-bottom: .4rem; color: var(--dim);
                   font-family: var(--mono); letter-spacing: .14em;
                   text-transform: uppercase; font-weight: 400; }
/* Scoped to the grid: `.imprint p` also matched the chapter line above it, zeroed
   its margin, and printed the two on top of each other. */
.imprint-grid p { color: var(--dim); font-size: .82rem; margin: 0; }
footer { padding: 2rem 6vw 3.5rem; color: var(--dim); font-size: .82rem;
         border-top: 1px solid var(--line); }
code { background: var(--card); padding: .1rem .35rem; font-family: var(--mono);
       font-size: .78rem; }

@media (max-width: 1100px) {
  .readout { grid-template-columns: repeat(2, 1fr); }
  .readout .cell:nth-child(3) { border-left: 0; padding-left: 6vw; }
}
@media (max-width: 900px) {
  .hero { grid-template-columns: 1fr; gap: 1.5rem; padding: 2rem 6vw 2.6rem; }
  .alarm { justify-self: stretch; max-width: none; }
  .banner-says { position: static; text-align: left; margin-top: .6rem; }
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
                done: false, pages: new Map(), clips: new Map(),
                keptKeys: new Set(), staged: null, csvUrl: '', drawingKept: 0,
                reels: new Map(), reelNow: [], rects: new Map(), painted: -1,
                filmTimer: null };

const el = (id) => document.getElementById(id);

const say = (n) => n.toLocaleString();

/* Wide enough for the pictures to scroll in their own box, or narrow enough that
   the page's own scroll is the only one worth having. */
const paged = () => window.matchMedia('(min-width: 900px)').matches;

async function boot() {
  // Read before anything writes: settling the wall rewrites the address bar, and
  // the moment somebody was sent would be gone before it was opened.
  const asked = askedFor();
  try {
    state.index = await (await fetch(`${TILES}/index.json`)).json();
  } catch (err) {
    // Two different problems with the same symptom - a page and no pictures - and
    // the wrong answer to one of them is to rebuild a store that is already there.
    // A browser will not let a page opened from a disk read the files beside it.
    el('wall').innerHTML = location.protocol === 'file:'
      ? '<p class="empty">This page was opened from a disk rather than from a server, '
        + 'and a browser will not let it read the pictures beside it. Serve the '
        + 'folder instead - <code>python3 -m http.server</code> in this directory, '
        + 'then open <code>http://localhost:8000</code>. The reports need it too.</p>'
      : '<p class="empty">No pictures were written beside this page. '
        + 'Run <code>collect site</code> where the reports are.</p>';
    return;
  }
  // What was kept first: the tiles read it as they are drawn.
  refreshKept();
  drawJumps();
  focusOn(...fromHash());
  watchTheWall();
  wireTheStage();
  drawKept();
  openAsked(asked);
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

/** Where the URL says to be: `#t=Animalia/Porifera`, or `!Porifera` for a rest arc,
 *  and `&a=EX2301/…mp4/259.4` for a moment somebody opened.
 *
 * A branch worth showing someone is worth being able to send them - most of all
 * Undecided, which is the one anybody will want to argue about - and so is a
 * sighting. A link to this page is a link to what was on the screen. */
function fromHash() {
  const raw = new URLSearchParams((location.hash || '').slice(1)).get('t') || '';
  if (!raw) return [[]];
  const [trail, only] = raw.split('!');
  const path = trail.split('/').filter(Boolean);
  return [path, only || null];
}

function writeHash() {
  const trail = state.path.join('/') + (state.only ? `!${state.only}` : '');
  const parts = [];
  if (trail) parts.push(`t=${encodeURIComponent(trail)}`);
  const staged = state.staged;
  if (staged && staged.animal && staged.animal.t && !el('stage').hidden) {
    parts.push('a=' + encodeURIComponent(
      [staged.animal.e, staged.animal.r, staged.animal.s].join('/')));
  }
  history.replaceState(null, '', parts.length ? `#${parts.join('&')}` : location.pathname);
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
      // Undecided sits inside Unplaced, so it is one click in and not a second
      // button beside the thing that contains it.
      odd.push({ node: kingdom, path: [kingdom.name] });
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
  writeHash();
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
    animals.forEach((animal, index) => {
      const still = stills.slice(at, at + animal.l);
      at += animal.l;
      wall.insertBefore(tileFor(animal, still, slug, next.page, index), el('more'));
    });
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

/** One frame of the contact sheet: the picture, two facts over it, and a way in.
 *
 * How sure the detector was and how long the animal stayed in view at the top, the
 * name at the bottom, both only while the pointer is on it. There is no play badge:
 * nearly every tile has a film, so a mark on nearly every tile said nothing and
 * cost a corner of the picture.
 *
 * Clicking it opens the footage at the second it was cut from, and the star keeps
 * the sighting. */
function tileFor(animal, still, slug, page, at) {
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
  figure.appendChild(starFor(animal, slug, page, at));
  figure.title = `${animal.t} · ${animal.e} · ${clock(animal.s)} · ${animal.n} `
    + `look${animal.n === 1 ? '' : 's'}`
    + (animal.a < 1 ? ` · agreed ${Math.round(animal.a * 100)}%` : '')
    + ' — click to open the footage here';
  const open = () => openStage(animal, slug, page, at, img.src);
  figure.addEventListener('click', open);
  figure.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); open(); }
  });
  if (animal.m) animate(figure, img, slug, page, animal);
  return figure;
}

/** The star on a tile, lit when this sighting is one somebody kept. */
function starFor(animal, slug, page, at) {
  const star = document.createElement('button');
  const key = keyOf(animal);
  star.className = 'star' + (state.keptKeys.has(key) ? ' on' : '');
  star.dataset.key = key;
  star.textContent = state.keptKeys.has(key) ? '★' : '☆';
  star.title = 'keep this sighting';
  star.addEventListener('click', (event) => {
    event.stopPropagation();            // the tile itself opens the footage
    toggleKept(animal, slug, page, at);
  });
  return star;
}

/** How deep it was, where the vehicle's track said so. */
const deep = (metres) => (metres || metres === 0) ? `${Math.round(metres)} m` : '';

/** When it was filmed, in the archive's own UTC, trimmed to the minute. */
function stamp(when) {
  if (!when) return '';
  const said = String(when).replace('T', ' ').replace('+00:00', '');
  return escape(said.slice(0, 16)) + ' UTC';
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

/* ── the moment itself ─────────────────────────────────────────────────────── */

/** What the store knows about where an animal came from: the archive's own file for
 *  its recording, the frame its box is measured in, and the reel of everything else
 *  found in the same recording. */
const whereFrom = (animal) => (state.index.where || {})[animal.e] || {};
const fileOf = (animal) => (whereFrom(animal).videos || {})[animal.r] || '';
const frameOf = (animal) => whereFrom(animal).frame || [16, 9];
const reelName = (animal) => (whereFrom(animal).takes || {})[animal.r] || '';

/** A couple of seconds before the detection, because an animal arriving on screen
 *  is most of what tells a reader whether the box is around anything. */
const LEAD_IN = 2;
/* How long one look is worth drawing a box for, and how far apart two looks can be
   and still be one animal moving rather than one animal twice. The detector looks
   about once a second, so between two looks the box is moved along - and across a
   longer gap it is not, because nothing was seen in between. */
const A_LOOK = 0.7, A_STRIDE = 2.5;

/** Every animal found in one recording, fetched once and kept. */
async function reelOf(animal) {
  const name = reelName(animal);
  if (!name) return [];
  if (!state.reels.has(name)) {
    state.reels.set(name, fetch(`${TILES}/reels/${name}.json`)
      .then(answer => answer.json()).catch(() => []));
  }
  return state.reels.get(name);
}

/** Open the footage at the second this animal was found.
 *
 * Nothing is copied or re-hosted: the archives serve their own files over byte
 * ranges, so a browser can seek into a 900 MB recording and fetch only the piece it
 * needs. Where the manifest named no URL - or the archive will not play in a page -
 * the crop is shown instead and the note says so.
 *
 * Every other animal the detector found in the same recording is drawn as well,
 * where and when it was found, because the question somebody has while a dive plays
 * in front of them is what else is on screen. Clicking one of those takes over: the
 * video keeps playing, and the header, the star and the address bar follow it. */
async function openStage(animal, slug, page, at, stillSrc) {
  const before = state.staged;
  const same = before && before.animal && !el('stage').hidden
    && before.animal.e === animal.e && before.animal.r === animal.r;
  state.staged = { animal, slug, page, at, still: stillSrc };
  const [wide, high] = frameOf(animal);
  const file = fileOf(animal);
  const from = Math.max(0, (animal.s || 0) - LEAD_IN);
  if (!same) {
    const play = el('stagePlay');
    play.style.aspectRatio = `${wide} / ${high}`;
    play.replaceChildren(
      file ? footage(file, from, slug, page, at) : theCrop(stillSrc, animal, slug, page, at),
      boxLayer(wide, high));
    play.classList.toggle('no-box', !el('stageBox').checked);
    state.rects = new Map();
    state.reelNow = [];
    el('stageNote').textContent = file ? ''
      : 'No URL was recorded for this recording, so this is the crop and not the footage.';
    el('stageFile').href = file ? `${file}#t=${from.toFixed(1)}` : '#';
    el('stageFile').hidden = !file;
  }
  el('stage').hidden = false;
  sayStaged();
  if (same) { paintBoxes(true); return; }
  followAlong();
  state.reelNow = await reelOf(animal);
  paintBoxes(true);
}

/** Who is in focus: the header, the crop, the star, the way back, the address bar. */
function sayStaged() {
  const { animal, still } = state.staged;
  el('stageName').textContent = animal.t;
  el('stageFacts').textContent = [
    animal.c.toFixed(2),
    `${held(animal)} in view`,
    `${animal.n} look${animal.n === 1 ? '' : 's'}`,
  ].join(' · ');
  // Where and when it was, which is what anybody asks of a deep-sea picture after
  // what it is. The depth and the clock come out of the report; the recording is
  // the archive's own file name, and it is the thing to cite.
  el('stageWhere').innerHTML = [
    deep(animal.z), stamp(animal.w), `${escape(animal.e)} · ${clock(animal.s)}`,
    `<span class="reel">${escape(animal.r)}</span>`,
  ].filter(Boolean).join(' <span class="sep">·</span> ');
  // Whose frame this is, and what the name over it is worth. Both belong on the
  // thing itself: a crop travels, and it arrives without the page around it.
  const terms = whereFrom(animal);
  el('stageTerms').innerHTML = [
    terms.credit ? `<span class="credit">${escape(terms.credit)}</span>` : '',
    terms.licence ? escape(terms.licence) : '',
    `<span class="guess">${escape(animal.t)} is one detector's guess, not an `
      + `identification</span>`,
  ].filter(Boolean).join(' <span class="sep">·</span> ');
  const crop = el('stageCrop');
  crop.src = still || '';
  crop.hidden = !still;
  crop.alt = animal.t;
  el('stageStar').dataset.key = keyOf(animal);
  el('stageBack').textContent = `↺ back to ${clock(animal.s)}`;
  markKept();
  writeHash();
}

function footage(file, from, slug, page, at) {
  const video = document.createElement('video');
  video.src = `${file}#t=${from.toFixed(1)}`;
  video.controls = true;
  video.autoplay = true;
  video.muted = true;            // a page that makes noise unasked is a rude page
  video.playsInline = true;
  video.preload = 'metadata';
  video.addEventListener('loadedmetadata', () => {
    // The media fragment is a request, not a promise; some servers ignore it.
    if (Math.abs(video.currentTime - from) > 1) video.currentTime = from;
  });
  video.addEventListener('error', () => {
    // The archive is down, or the file is one this browser will not decode. Either
    // way there is something to show: the crop, and the clip cut around it.
    el('stageNote').textContent = 'The archive would not play this file here.';
    const staged = state.staged;
    const crop = theCrop(staged && staged.still, staged ? staged.animal : {},
                         slug, page, at);
    video.replaceWith(crop);
  });
  return video;
}

/** The animal itself, where the footage will not play.
 *
 * The still is what the tile showed and it is the least this can do; the clip the
 * detector cut is six frames of the same seconds, and it is already in the store.
 * A reader who cannot get the recording should at least see the animal move. */
function theCrop(stillSrc, animal, slug, page, at) {
  const img = document.createElement('img');
  img.src = stillSrc || '';
  img.alt = animal.t;
  playTheClip(img, slug, page, at);
  return img;
}

async function playTheClip(img, slug, page, at) {
  clearInterval(state.filmTimer);
  state.filmTimer = null;
  if (slug === undefined || page === undefined || at === undefined) return;
  try {
    const [{ animals }, blob] = await Promise.all([pageOf(slug, page), clipsOf(slug, page)]);
    const animal = animals[at];
    if (!animal || !animal.m || !(animal.f || []).length) return;
    let from = clipStart(animals, animal);
    const frames = animal.f.map(size => {
      const picture = asPicture(blob.slice(from, from + size));
      from += size;
      return picture;
    });
    if (!frames.length || el('stage').hidden) return;
    let step = 0;
    state.filmTimer = setInterval(() => {
      if (el('stage').hidden) { clearInterval(state.filmTimer); return; }
      img.src = frames[step++ % frames.length];
    }, 140);
    el('stageNote').textContent = el('stageNote').textContent
      + ' Playing the seconds the detector cut instead.';
  } catch { /* the still is what there is */ }
}

/** The layer the boxes are drawn on, in the coordinates of the analysed frame.
 *
 * `meet` rather than `none`: the video is fitted into the stage the same way, so
 * the two letterbox together whatever the window does to them. */
function boxLayer(wide, high) {
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('id', 'stageBoxes');
  svg.setAttribute('viewBox', `0 0 ${wide} ${high}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  return svg;
}

/** Where an animal was at this second, between the two looks nearest to it.
 *
 * One box for the whole time an animal was in view is wrong the moment anything
 * swims: it was drawn at one second and the frame has moved on. So the box moves
 * between the looks the detector actually had, and where two looks are too far
 * apart to call it movement, it is simply not drawn. */
function boxAt(entry, now) {
  const looks = entry.k || [];
  if (!looks.length) return null;
  if (now < looks[0][0] - A_LOOK) return null;
  if (now > looks[looks.length - 1][0] + A_LOOK) return null;
  const after = looks.findIndex(look => look[0] >= now);
  if (after < 0) return looks[looks.length - 1].slice(1);
  if (after === 0) return looks[0].slice(1);
  const before = looks[after - 1], next = looks[after];
  const span = next[0] - before[0];
  if (span > A_STRIDE) {
    if (now - before[0] <= A_LOOK) return before.slice(1);
    if (next[0] - now <= A_LOOK) return next.slice(1);
    return null;
  }
  const part = span > 0 ? (now - before[0]) / span : 0;
  return [1, 2, 3, 4].map(i => before[i] + (next[i] - before[i]) * part);
}

const sameSighting = (entry, animal) =>
  Boolean(animal) && entry.t === animal.t && Math.abs(entry.s - animal.s) < 0.06;

/** Draw every animal that belongs on screen at the moment being played. */
function paintBoxes(force) {
  const svg = el('stageBoxes');
  if (!svg) return;
  // Until the video has data and has got to where it was asked to start, its
  // clock reads zero - and boxes drawn for the first second of a recording are
  // some other animal's, or none. The second somebody clicked is the truth until
  // the footage catches up with it.
  const video = el('stagePlay').querySelector('video');
  const live = video && video.readyState >= 2 && video.currentTime > 0;
  const now = live ? video.currentTime : (state.staged ? state.staged.animal.s : 0);
  if (!force && Math.abs(now - state.painted) < 0.04) return;
  state.painted = now;
  const focus = state.staged && state.staged.animal;
  const gone = new Set(state.rects.keys());
  (state.reelNow || []).forEach((entry, index) => {
    const box = boxAt(entry, now);
    if (!box) return;
    gone.delete(index);
    let drawn = state.rects.get(index);
    if (!drawn) {
      drawn = { rect: document.createElementNS(SVGNS, 'rect'),
                label: document.createElementNS(SVGNS, 'text') };
      drawn.rect.addEventListener('click', () => pickFromReel(entry));
      // Only the animal in focus is named on screen. A crowded seabed puts
      // twenty-five boxes up at once, and twenty-five names over them is a wall of
      // text with a dive behind it - so the rest say who they are when pointed at.
      drawn.rect.addEventListener('mouseenter', () => {
        drawn.label.textContent = entry.t;
      });
      drawn.rect.addEventListener('mouseleave', () => {
        drawn.label.textContent = sameSighting(entry, state.staged
          && state.staged.animal) ? entry.t : '';
      });
      const title = document.createElementNS(SVGNS, 'title');
      title.textContent = `${entry.t} — ${entry.c.toFixed(2)}, ${clock(entry.s)}`;
      drawn.rect.appendChild(title);
      state.rects.set(index, drawn);
      svg.append(drawn.rect, drawn.label);
    }
    const here = sameSighting(entry, focus);
    if (drawn.label.textContent !== entry.t || !here) {
      drawn.label.textContent = here ? entry.t : '';
    }
    drawn.rect.setAttribute('x', String(box[0]));
    drawn.rect.setAttribute('y', String(box[1]));
    drawn.rect.setAttribute('width', String(Math.max(1, box[2] - box[0])));
    drawn.rect.setAttribute('height', String(Math.max(1, box[3] - box[1])));
    drawn.rect.setAttribute('class', here ? 'here' : '');
    drawn.label.setAttribute('x', String(box[0]));
    drawn.label.setAttribute('y', String(Math.max(9, box[1] - 3)));
    drawn.label.setAttribute('class', here ? 'here' : '');
  });
  for (const index of gone) {
    const drawn = state.rects.get(index);
    drawn.rect.remove();
    drawn.label.remove();
    state.rects.delete(index);
  }
}

/** Follow the playhead for as long as the stage is open. */
function followAlong() {
  if (el('stage').hidden) return;
  paintBoxes(false);
  if (window.requestAnimationFrame) requestAnimationFrame(followAlong);
}

/** Take over the stage with another animal from the same recording.
 *
 * No seeking: the reader is watching, and this animal is on screen now. The way
 * back to its own moment is the button in the corner. */
async function pickFromReel(entry) {
  const staged = state.staged;
  if (!staged || !staged.animal) return;
  const own = (entry.k || []).find(look => Math.abs(look[0] - entry.s) < 0.06)
    || (entry.k || [])[0] || [];
  const animal = { t: entry.t, e: staged.animal.e, r: staged.animal.r, s: entry.s,
                   c: entry.c, d: entry.d, n: entry.n, a: 1, b: own.slice(1) };
  const still = await stillOf(entry.g, entry.p, entry.at);
  openStage(animal, entry.g, entry.p, entry.at, still);
}

/** One tile's picture, cut out of the page it lives on. */
async function stillOf(slug, page, at) {
  try {
    const { animals, stills } = await pageOf(slug, page);
    const animal = animals[at];
    if (!animal) return '';
    let from = 0;
    for (const before of animals.slice(0, at)) from += before.l;
    return asPicture(stills.slice(from, from + animal.l));
  } catch { return ''; }
}

function shutStage() {
  clearInterval(state.filmTimer);
  state.filmTimer = null;
  const video = el('stagePlay').querySelector('video');
  if (video) {                    // or it goes on fetching the recording unwatched
    video.pause();
    video.removeAttribute('src');
    video.load();
  }
  el('stagePlay').replaceChildren();
  el('stage').hidden = true;
  state.staged = null;
  state.rects = new Map();
  writeHash();
}

/** The sighting the address bar asks for, if it asks for one.
 *
 * A link to this page is a link to what was on the screen: `#a=EX2301/…mp4/259.4`
 * opens that recording there, with that animal in focus, which is what somebody
 * sending the link meant to send. */
const askedFor = () =>
  new URLSearchParams((location.hash || '').slice(1)).get('a') || '';

async function openAsked(asked) {
  if (!asked) return;
  const [expedition, recording, second] = asked.split('/');
  if (!expedition || !recording || second === undefined) return;
  const reel = await reelOf({ e: expedition, r: recording });
  const entry = reel.find(one => Math.abs(one.s - Number(second)) < 0.06);
  if (!entry) return;
  state.staged = { animal: { e: expedition, r: recording } };   // for pickFromReel
  state.reelNow = reel;
  await pickFromReel(entry);
}

/* ── what somebody kept ───────────────────────────────────────────────────── */

/* In this browser and nowhere else. There is no account to have and nothing is
   sent anywhere, so `localStorage` is the whole of it - which also means a reader
   who clears their browser loses them, and the CSV is how they keep them. */
const KEPT = 'pp-deepsea-kept';
const keyOf = (animal) => `${animal.e}|${animal.r}|${animal.s}|${animal.t}`;

function kept() {
  try { return JSON.parse(localStorage.getItem(KEPT)) || []; } catch { return []; }
}

function refreshKept() {
  state.keptKeys = new Set(kept().map(entry => entry.k));
}

function keepThese(list) {
  try { localStorage.setItem(KEPT, JSON.stringify(list)); }
  catch { /* a private window, or a full one. The page still works. */ }
  refreshKept();
  markKept();
  drawKept();
}

/** Star or unstar one sighting.
 *
 * Enough of it is written down to find the moment again in the archive - the
 * recording, the second, the box - and where it sits in the store, so its picture
 * can be drawn again without keeping a copy of it. */
function toggleKept(animal, slug, page, at) {
  const list = kept(), key = keyOf(animal);
  const found = list.findIndex(entry => entry.k === key);
  if (found >= 0) list.splice(found, 1);
  else list.push({ k: key, slug, page, at, t: animal.t, e: animal.e, r: animal.r,
                   s: animal.s, c: animal.c, d: animal.d || 0, n: animal.n,
                   b: animal.b || [], v: fileOf(animal), f: frameOf(animal) });
  keepThese(list);
}

function markKept() {
  for (const star of document.querySelectorAll('.star[data-key]')) {
    const on = state.keptKeys.has(star.dataset.key);
    star.classList.toggle('on', on);
    star.textContent = on ? '★' : '☆';
    star.title = on ? 'kept - click to forget it' : 'keep this sighting';
  }
}

/** The favourites, drawn from the store rather than from a copy of the pictures.
 *
 * Built whole and then put in place, because starring two tiles quickly had two of
 * these running at once: both cleared the wall, both waited on a page, and both
 * appended what they had, so a reader with one favourite was shown two of it. */
async function drawKept() {
  const mine = ++state.drawingKept;
  const list = kept();
  el('kept').hidden = !list.length;
  el('keptCsv').href = list.length ? asCsv(list) : '#';
  const tiles = [];
  for (const entry of list) {
    try {
      const { animals, stills } = await pageOf(entry.slug, entry.page);
      const animal = animals[entry.at];
      if (!animal) continue;
      let at = 0;
      for (const before of animals.slice(0, entry.at)) at += before.l;
      tiles.push(tileFor(animal, stills.slice(at, at + animal.l),
                         entry.slug, entry.page, entry.at));
    } catch { /* a favourite whose page will not load is one tile fewer */ }
  }
  if (mine !== state.drawingKept) return;     // a later draw has taken over
  el('keptWall').replaceChildren(...tiles);
  markKept();
}

/** The favourites as a file somebody else could use.
 *
 * The recording, the second and the box are the useful part: with those three a
 * reader opens the archive's own file and finds the same animal, whatever happens
 * to this page. */
function asCsv(list) {
  // `taxon_is_a_guess` is a column rather than a footnote because a CSV is the one
  // thing here that leaves and gets read somewhere else, by somebody who never saw
  // the disclaimer. Same for whose footage each row came from.
  const head = ['taxon', 'taxon_is_a_guess', 'confidence', 'seconds_in_view', 'looks',
                'expedition', 'recording', 'second', 'when', 'depth_m',
                'x0', 'y0', 'x1', 'y1', 'frame_width', 'frame_height', 'video',
                'credit', 'licence'];
  const rows = [head, ...list.map(entry => {
    const box = (entry.b || []).concat(['', '', '', '']).slice(0, 4);
    const frame = (entry.f || []).concat(['', '']).slice(0, 2);
    const terms = (state.index.where || {})[entry.e] || {};
    return [entry.t, 'yes, from an object detector', entry.c, entry.d, entry.n,
            entry.e, entry.r, entry.s, entry.w || '',
            entry.z === undefined || entry.z === null ? '' : entry.z,
            ...box, ...frame, entry.v || '',
            terms.credit || '', terms.licence || ''];
  })];
  const csv = rows.map(row => row.map(cell => {
    const text = cell === undefined || cell === null ? '' : String(cell);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }).join(',')).join('\n');
  if (state.csvUrl) URL.revokeObjectURL(state.csvUrl);
  state.csvUrl = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  return state.csvUrl;
}

/** The controls that are on the page once rather than once per tile. */
function wireTheStage() {
  el('stageShut').addEventListener('click', shutStage);
  el('stage').addEventListener('click', (event) => {
    if (event.target === el('stage')) shutStage();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !el('stage').hidden) shutStage();
  });
  el('stageBox').addEventListener('change', () => {
    el('stagePlay').classList.toggle('no-box', !el('stageBox').checked);
  });
  el('stageBack').addEventListener('click', () => {
    const video = el('stagePlay').querySelector('video');
    if (!video || !state.staged) return;
    video.currentTime = Math.max(0, (state.staged.animal.s || 0) - LEAD_IN);
    video.play().catch(() => { /* a paused video at the right second is fine */ });
  });
  el('stageStar').addEventListener('click', () => {
    const staged = state.staged;
    if (staged) toggleKept(staged.animal, staged.slug, staged.page, staged.at);
  });
  el('keptClear').addEventListener('click', () => keepThese([]));
}

const escape = (text) => String(text).replace(/[<>&]/g, ch =>
  ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[ch]);

const clock = (seconds) => {
  const whole = Math.floor(seconds);
  return `${String(Math.floor(whole / 60)).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`;
};

boot();
"""
