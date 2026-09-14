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
import re
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


def _fill(name: str, values: Dict[str, object]) -> str:
    """The page, with what this collection turned out to hold poured into it.

    `{{name}}` and nothing cleverer. The page is one shape with sixteen holes in
    it, and a template language would be a dependency and a second syntax to read
    around. An unknown name raises rather than rendering itself, because a page
    with `{{animals}}` printed across it is a page somebody ships.
    """
    def one(match):
        key = match.group(1)
        if key not in values:
            raise KeyError(f"{name} asks for {key!r}, which render() does not set")
        return str(values[key])

    return re.sub(r"\{\{(\w+)\}\}", one, _asset(name))


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
    return _fill("landing.html", {
        "style": style(),
        "script": script(),
        "where_the_data_is": _where_the_data_is(data_url),
        "expeditions": ran,
        "today": datetime.now().strftime("%Y-%m-%d"),
        "footage_read": _cell(_clock(seconds), "footage read"),
        # An expedition nobody has listed yet has no denominator, and one whose
        # animals have not been counted still knows how many slices held one.
        "recordings": _cell(f"{processed:,} / {listed:,}" if listed else f"{processed:,}",
                            "recordings"),
        "animals": (_cell(f"{animals:,}", "animals") if animals
                    else _cell(f"{seen:,}", "slices with an animal")),
        "names_given": _cell(f"{taxa:,}", "names given"),
        "combined_report": _together(combined),
        "fleet": "".join(_fleet_row(r, data_url) for r in rows),
        "asset": ASSET,
        "lookup": _lookup(index),
    })


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


