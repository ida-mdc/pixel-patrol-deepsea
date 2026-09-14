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

import functools
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# The page's own JavaScript and CSS, which are JavaScript and CSS and live in files
# that say so. They were strings in this module until they were sixteen hundred and
# four hundred lines of them, at which point nothing could check their syntax, no
# formatter would touch them and the test that exercises the page had to cut the
# script back out of the Python with a string split. They are inlined into the one
# file the page is, which is the point of the page: a collection is a folder you can
# copy, and `index.html` opening with no build step is worth more than caching two
# assets that only it uses.
PAGE = Path(__file__).parent / "page"


@functools.lru_cache(maxsize=None)
def _asset(name: str) -> str:
    return (PAGE / name).read_text()


def script() -> str:
    """The page's behaviour."""
    return _asset("landing.js")


def style() -> str:
    """The page's look."""
    return _asset("landing.css")

ASSET = "assets/pixel-patrol-deepsea.png"
# Every recording that has been read, in the order it was filmed, one stripe each.
# Written by `banner.py` from the colours the processors measured.
BANNER = "assets/colours.png"


def render(rows, index: Optional[Dict] = None, scores: Optional[Dict] = None,
           data_url: str = "") -> str:
    from pixel_patrol_deepsea.catalogue_page import _clock, _report_url, data_at, tiles_at
    from pixel_patrol_deepsea.merge import EVERYTHING

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
    combined = (_report_url(data_at(data_url, f"parquet/{EVERYTHING}.parquet"),
                            group="expedition")
                if len(everything) > 1 else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deep-sea footage collection</title>
<style>\n{style()}</style></head>
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
      <div class="find" role="combobox" aria-haspopup="listbox" aria-owns="findList">
        <input id="find" type="search" autocomplete="off" spellcheck="false"
               placeholder="find a name" aria-autocomplete="list"
               aria-controls="findList" aria-label="find a name the detector gave">
        <ul class="found" id="findList" role="listbox" hidden></ul>
      </div>
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
    <h2 id="keptTitle">Your favourites</h2>
    <p class="lede" id="keptWhat"></p>
    <p class="kept-does">
      <a class="cta" id="keptCsv" download="deepsea-favourites.csv" href="#"
         >download the csv &darr;</a>
      <button class="quiet" id="keptShare">copy a link to these</button>
      <button class="quiet" id="keptClear">forget all of them</button>
      <button class="quiet" id="keptMine" hidden>show mine instead</button>
      <button class="quiet keep-these" id="keptTake" hidden>keep these</button>
    </p>
    <p class="kept-link" id="keptLink" hidden>
      <input id="keptUrl" readonly><span id="keptSaid"></span></p>
    <div class="wall kept-wall" id="keptWall"></div>
  </section>

  <section class="fleet">
    <p class="chapter">03 / the expeditions</p>
    <h2>Where the footage came from</h2>
    {_together(combined)}
    <table class="fleet-table">
      <thead><tr><th>expedition</th><th>when</th><th>analysed</th><th>footage</th>
        <th>animals</th><th>names</th><th></th></tr></thead>
      <tbody>{"".join(_fleet_row(r, data_url) for r in rows)}</tbody>
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
<script>{_where_the_data_is(data_url)}\n{script()}</script>
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


def _where_the_data_is(data_url: str) -> str:
    """One line of JavaScript, when the store is not beside the page."""
    from pixel_patrol_deepsea.catalogue_page import tiles_at

    if not data_url.strip():
        return ""
    return f"window.PP_TILES = {json.dumps(tiles_at(data_url))};\n"


def _fleet_row(row, data_url: str = "") -> str:
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
      <td class="num">{_link(row, data_url)}</td>
    </tr>"""


