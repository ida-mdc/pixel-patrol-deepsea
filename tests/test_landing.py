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


def test_the_disclaimer_says_all_of_it_where_it_can_be_seen(tmp_path):
    """Six headed paragraphs became six lines, and then stopped hiding.

    Behind a summary they were read by nobody, which is the same as not writing
    them. One line each, in the box, visible.
    """
    page = render(_two_expeditions(tmp_path))
    warning = page[page.index('<section class="warning"'):
                   page.index("</section>", page.index('<section class="warning"'))]
    assert "<details" not in warning and "<summary" not in warning
    assert warning.count("<li>") == 6
    # Short: the whole box is nearer a paragraph than a page.
    assert len(re.sub(r"<[^>]+>", " ", warning).split()) < 200
    for said in ("upper bound", "never trained on", "six points of recall",
                 "about 1.8 entries", "lamps"):
        assert said in warning, said


def test_the_taxonomy_is_grouped_the_way_the_reports_group_it(tmp_path):
    """The row of ways in used to be the four biggest branches anywhere in the tree.

    That put a kingdom, a phylum and a class beside each other with nothing saying
    so, and no way back to everything. Phylum is the rank the reports colour and
    split by, and `GROUP_RANK` is where that is written down.
    """
    from pixel_patrol_deepsea.catalogue_page import GROUP_RANK

    assert GROUP_RANK == "phylum"
    page = render(_two_expeditions(tmp_path))
    assert "Grouped by phylum" in page
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
