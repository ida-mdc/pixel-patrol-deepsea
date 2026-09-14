"""The page a collection opens on.

What is checked here is what a reader is promised: a header that does not repeat
the numbers underneath it, one line of disclaimer rather than six paragraphs of
it, a way into the combined report that says what it is, and a description of
whatever branch of the taxonomy is in focus.
"""

import re

from pixel_patrol_deepsea.catalogue_page import read_progress, render

from tests.test_catalogue_page import _collection, _slices


def _clock_of(page):
    """The footage clock, wherever the readout put it."""
    return re.search(r"<b>\d+:\d\d:\d\d</b>", page)


def _two_expeditions(tmp_path):
    _collection(tmp_path, listed=10, report_rows=_slices(), expedition="EX2107")
    _collection(tmp_path, listed=6, report_rows=_slices(), expedition="EX2205")
    return read_progress(tmp_path)


def test_the_title_leaves_the_numbers_to_the_numbers(tmp_path):
    """`22:06:44 of footage nobody had watched` said two things it should not.

    The clock is in the summary a centimetre below it, and whether anybody had
    watched the footage is not something this page knows - somebody filmed it.
    """
    page = render(_two_expeditions(tmp_path))
    title = re.search(r"<h1>(.*?)</h1>", page, re.S).group(1)
    assert not re.search(r"\d", title), title
    assert "nobody" not in title.lower()
    # ...and the numbers are still on the page, in the readout under it.
    assert "footage read" in page and _clock_of(page)


def test_the_header_does_not_carry_pictures_of_its_own(tmp_path):
    """The hero used to drift nine crops across the bottom of a full-height header.

    There are tens of thousands of them a screen further down, which is what the
    page is for, so the header does not spend a viewport advertising them.
    """
    page = render(_two_expeditions(tmp_path))
    assert "hero-tiles" not in page and "heroTiles" not in page
    assert "min-height: 100vh" not in page


def test_the_way_into_the_collection_says_what_it_opens(tmp_path):
    """"Open all 9 expeditions together" did not warn anybody it holds no pictures.

    It is the statistics over the collection, it is grouped by expedition because
    comparing them is the only reason to put them in one report, and a reader who
    follows it looking for animals should have been told before they clicked.
    """
    page = render(_two_expeditions(tmp_path))
    link = re.search(r'<a class="cta" href="([^"]+)">(.*?)</a>', page, re.S)
    assert "_everything.parquet" in link.group(1)
    assert "group=expedition" in link.group(1).replace("&amp;", "&")
    assert not re.search(r"\d", link.group(2)), link.group(2)
    assert "No pictures" in page and "grouped by expedition" in page


def test_the_disclaimer_stands_in_the_header(tmp_path):
    """Six headed paragraphs became six lines, stopped hiding, and moved up.

    It reads before the thing it is a warning about rather than after it, and it
    says what the page is: a prototype, not a survey.
    """
    page = render(_two_expeditions(tmp_path))
    hero = page[page.index('<section class="hero"'):page.index("</section>")]
    alarm = hero[hero.index('<aside class="alarm"'):]
    assert "<details" not in page and "<summary" not in page
    assert "Prototype" in alarm
    said = " ".join(re.sub(r"<[^>]+>", " ", alarm).split())
    assert "pulling statistics and animal names" in said
    assert alarm.count("<li>") == 5
    # Short: the whole box is nearer a paragraph than a page.
    assert len(re.sub(r"<[^>]+>", " ", alarm).split()) < 130
    for said in ("shortlist", "never read", "upper bounds", "six points of recall",
                 "lamps"):
        assert said in alarm, said


def test_the_taxonomy_is_grouped_the_way_the_reports_group_it(tmp_path):
    """The row of ways in used to be the four biggest branches anywhere in the tree.

    That put a kingdom, a phylum and a class beside each other with nothing saying
    so, and no way back to everything. Phylum is the rank the reports colour and
    split by, and it is the one the row of ways in is built from.
    """
    page = render(_two_expeditions(tmp_path))
    assert "grouped by the ranks the World" in page
    # The kingdom's children are the phyla, and everything is one of the buttons.
    assert "for (const phylum of kingdom.children" in page
    assert "path: [], all: true" in page


def test_only_the_articles_that_exist_are_linked(tmp_path):
    """A link to an article that is not there is worse than no link at all.

    `fetch_wikipedia` asks which names have one; the page carries the answers for
    the names in this tree and nothing else, so a collection of one midwater dive
    does not ship an entry about barnacles.
    """
    import json

    from pixel_patrol_deepsea.fetch_wikipedia import load_articles

    index = {"tree": {"name": "everything", "count": 3,
                      "children": [{"name": "Animalia", "count": 3, "children": [
                          {"name": "Porifera", "count": 3, "taxa": ["Asbestopluma"]}]}]},
             "taxa": {"Asbestopluma": {"slug": "asbestopluma", "count": 3, "pages": 1,
                                       "above": ["Animalia", "Porifera", "Demospongiae",
                                                 "Poecilosclerida", "Cladorhizidae",
                                                 "Asbestopluma"]}}}
    page = render(_two_expeditions(tmp_path), index)
    lookup = json.loads(re.search(r"const LOOKUP = (\{.*?\});", page, re.S).group(1))
    assert lookup["Porifera"] == "Sponge", "the article title is the plain word for it"
    assert set(lookup) <= set(index["taxa"]["Asbestopluma"]["above"]) | {
        "everything", "Animalia", "Porifera"}
    assert "Cnidaria" not in lookup
    # ...and every title the page offers is one the fetch actually found.
    articles = load_articles()
    assert all(articles.get(name) == title for name, title in lookup.items())


def test_the_page_describes_nothing_in_its_own_words(tmp_path):
    """The box says what the detector called it, what the register knows and what
    the encyclopaedia calls it. A paragraph of natural history written to fill the
    space would be the one thing on the page with no source behind it."""
    page = render(_two_expeditions(tmp_path))
    assert "descriptions" not in page
    # The two exceptions are names this project and the detector made up, which
    # nobody else is going to explain.
    assert "const OURS" in page
    assert page.count("Undecided:") == 1 and page.count("Unplaced:") == 1


def test_the_header_opens_on_the_collection_s_own_colours(tmp_path):
    """The banner is the data: one stripe per recording, precomputed by `banner.py`
    because the alternative is reading six gigabytes of parquet in a browser."""
    from pixel_patrol_deepsea.landing import BANNER

    page = render(_two_expeditions(tmp_path))
    hero = page[page.index('<section class="hero"'):page.index("</section>")]
    assert f'url("{BANNER}")' in page
    assert "one stripe each" in hero
    # ...and the drawing that used to be here is now down with the credit for it.
    assert page.index('class="patrol"') > page.index('<section class="credits"')


def test_the_page_says_who_is_responsible_for_it(tmp_path):
    page = render(_two_expeditions(tmp_path))
    imprint = page[page.index('<section class="imprint"'):]
    said = " ".join(re.sub(r"<[^>]+>", " ", imprint).split())
    for word in ("§5 DDG", "Deborah Schmidt", "Max-Delbrück-Centrum", "13125 Berlin",
                 "at your own risk", "No recording is hosted here",
                 "deborah.schmidt@mdc-berlin.de"):
        assert word in said, word
    assert "+49" not in said and "Phone" not in said


def test_the_expedition_name_is_the_link_to_the_expedition(tmp_path):
    """The report is the point of the row, so it gets the loud button at the end;
    the ship's own page is what the expedition is called."""
    page = render(_two_expeditions(tmp_path))
    row = page[page.index("<tbody>"):page.index("</tbody>")]
    assert '<b><a class="mission"' in row
    assert row.index('class="mission"') < row.index('class="open"')


def test_the_page_says_whose_footage_each_picture_is(tmp_path):
    """Three archives, three different answers, and a page that shows crops of all
    of them at once. Read off their own terms in September 2026."""
    page = render(_two_expeditions(tmp_path))
    said = " ".join(re.sub(r"<[^>]+>", " ", page).split())
    assert "public domain" in said and "NOAA Ocean Exploration" in said
    # MBARI's benchmark is share-alike, which the page said was CC BY for a while.
    assert "CC BY-SA 4.0" in said
    assert "arXiv:2509.03499" in said
    # The observatory names no licence and requires two acknowledgements.
    assert "National Science Foundation" in said and "WHOI OOI Program Office" in said
    assert "doi:10.14284/170" in said          # WoRMS, whose text is CC BY


def test_a_name_is_never_presented_as_an_identification(tmp_path):
    """The one thing this page must not let somebody walk away believing."""
    page = render(_two_expeditions(tmp_path))
    said = " ".join(re.sub(r"<[^>]+>", " ", page).split())
    assert "Every name on this page is an automated guess" in said
    assert "not an identification" in said
    # ...in the overlay over the picture itself, and in the CSV that leaves with
    # somebody who never saw the page.
    assert "is one detector's guess, not an" in page
    assert "taxon_is_a_guess" in page


# ── a page and its gigabytes in different places ──────────────────────────────

def test_by_default_everything_is_beside_the_page(tmp_path):
    """A collection on a laptop is one folder, and that has to stay the easy case."""
    page = render(_two_expeditions(tmp_path))
    # The script names `window.PP_TILES` either way - what says nothing was set is
    # that nothing assigned to it.
    assert "window.PP_TILES =" not in page
    assert "|| 'tiles'" in page                   # what the script falls back to
    assert "data=../parquet/" in page


def test_the_store_and_the_reports_can_live_somewhere_else(tmp_path):
    """Fifteen gigabytes of pictures do not go in a repository, and a hundred
    kilobytes of page does not need a storage facility."""
    where = "https://hifis-storage.desy.de/Helmholtz/HIP/collaborations/PixelPatrolDeepSea"
    page = render(_two_expeditions(tmp_path), data_url=where)
    assert f'window.PP_TILES = "{where}/tiles"' in page
    assert f"data={where}/parquet/" in page
    assert "data=../parquet/" not in page


def test_a_trailing_slash_does_not_double_up(tmp_path):
    page = render(_two_expeditions(tmp_path), data_url="https://host/coll/")
    assert '"https://host/coll/tiles"' in page
    assert "//tiles" not in page.replace("https://", "")


def test_the_taxonomy_and_the_assets_stay_with_the_page(tmp_path):
    """The tree fetches `taxonomy.json` relative to the viewer, and the banner is
    two megabytes - both belong wherever the page is, not on the storage."""
    page = render(_two_expeditions(tmp_path), data_url="https://host/coll")
    assert "assets/colours.png" in page
    assert "https://host/coll/assets" not in page
