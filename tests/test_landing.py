"""The page a collection opens on.

What is checked here is what a reader is promised: a header that does not repeat
the numbers underneath it, one line of disclaimer rather than six paragraphs of
it, a way into the combined report that says what it is, and a description of
whatever branch of the taxonomy is in focus.
"""

import re

from pixel_patrol_deepsea.catalogue_page import read_progress, render

from tests.test_catalogue_page import _collection, _slices


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
    # ...and the numbers are still on the page, in the summary under it.
    assert "of footage read" in page


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


def test_the_disclaimer_is_one_line_and_the_detail_is_behind_it(tmp_path):
    page = render(_two_expeditions(tmp_path))
    warning = page[page.index('<section class="warning"'):page.index("</section>", page.index('<section class="warning"'))]
    alarm = re.search(r'<p class="alarm">(.*?)</p>', warning, re.S).group(1)
    assert "Disclaimer" in alarm
    assert len(re.sub(r"<[^>]+>", "", alarm).split()) < 60
    # The six caveats are still there, and a reader has to ask for them.
    assert warning.index("<details") < warning.index("A proof of concept")


def test_the_taxonomy_is_grouped_the_way_the_reports_group_it(tmp_path):
    """The row of ways in used to be the four biggest branches anywhere in the tree.

    That put a kingdom, a phylum and a class beside each other with nothing saying
    so, and no way back to everything. Phylum is the rank the reports colour and
    split by, and `GROUP_RANK` is where that is written down.
    """
    from pixel_patrol_deepsea.catalogue_page import GROUP_RANK

    assert GROUP_RANK == "phylum"
    page = render(_two_expeditions(tmp_path))
    assert "One button per phylum" in page
    # The kingdom's children are the phyla, and everything is one of the buttons.
    assert "for (const phylum of kingdom.children" in page
    assert "path: [], all: true" in page


def test_only_the_descriptions_of_what_was_found_are_written_in(tmp_path):
    """A collection of one midwater dive should not carry a note about barnacles."""
    index = {"tree": {"name": "everything", "count": 3,
                      "children": [{"name": "Animalia", "count": 3, "children": [
                          {"name": "Porifera", "count": 3, "taxa": ["Asbestopluma"]}]}]},
             "taxa": {"Asbestopluma": {"slug": "asbestopluma", "count": 3, "pages": 1,
                                       "above": ["Animalia", "Porifera", "Demospongiae",
                                                 "Poecilosclerida", "Cladorhizidae",
                                                 "Asbestopluma"]}}}
    page = render(_two_expeditions(tmp_path), index)
    notes = re.search(r"const ABOUT = (\{.*?\});", page, re.S).group(1)
    import json

    notes = json.loads(notes)
    assert "Porifera" in notes and "Asbestopluma" in notes
    # An ancestor a reader can land on is described; a phylum nothing was found in
    # is not.
    assert "Cladorhizidae" in notes
    assert "Cnidaria" not in notes and "Arthropoda" not in notes


def test_no_description_is_the_name_over_again():
    """The point is the plain words. `Myxiniformes: hagfish` earns its two words;
    `Myxiniformes: the myxiniformes` would be the sentence a reader already had."""
    from pixel_patrol_deepsea.descriptions import NOTES

    for name, note in NOTES.items():
        assert note.strip().endswith((".", "?")), name
        said = note.lower().replace(".", "").replace("the ", "").strip()
        assert said != name.lower(), name
