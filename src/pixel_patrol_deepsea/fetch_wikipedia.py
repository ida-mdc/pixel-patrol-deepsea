"""Find which taxon names have a Wikipedia article, once.

    python -m pixel_patrol_deepsea.fetch_wikipedia

The collection page can say how many animals it found under a name and link the
name's record in the World Register of Marine Species. Neither tells a reader what
the animal *is*: WoRMS is a nomenclatural register - ranks, authorities,
synonymies - and it has no descriptions to link to. The encyclopaedia does, and
whatever else is true of it, it is a source rather than something this project made
up.

So this asks Wikipedia which of the names actually have an article and writes the
answers beside the module as `wikipedia.json`. A fetch rather than a link built
from the name at run time, for two reasons:

    a guessed link is often wrong. `Abyssocucumis abyssorum`, `Paelopatides` and
    `Acanthamunnopsis milleri` have no article at all, and sending a reader to a
    404 is worse than sending them nowhere.

    the article title is itself worth having. `Holothuroidea` redirects to *Sea
    cucumber* and `Sagittoidea` to *Chaetognatha*, so resolving redirects gets the
    plain word for a Latin name - which is most of what somebody clicking on
    `Holothuroidea` wanted in the first place.

Every name the taxonomy knows is asked about, the detector's classes and the ranks
above them alike, because the page's tree is built out of both: a reader lands on
`Hexacorallia` as readily as on `Asbestopluma`.
"""

import argparse
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List

logger = logging.getLogger(__name__)

API = "https://en.wikipedia.org/w/api.php"
# Titles per request. The API takes 50 for an anonymous caller, so 600 names are
# twelve requests - which is why this is polite by construction.
AT_ONCE = 50
TIMEOUT = 30
ATTEMPTS = 3
# Wikimedia asks for a descriptive agent and blocks the default one.
AGENT = "pixel-patrol-deepsea/0.9 (taxon article lookup; https://github.com/ida-mdc/pixel-patrol)"

RANKS = ("kingdom", "phylum", "class", "order", "family", "genus", "species")


def wikipedia_path() -> Path:
    return Path(__file__).parent / "wikipedia.json"


def load_articles() -> Dict[str, str]:
    """Name -> article title, or an empty map if nobody has fetched them."""
    where = wikipedia_path()
    if not where.is_file():
        return {}
    try:
        return json.loads(where.read_text())
    except Exception as exc:
        logger.warning("cannot read %s: %s", where, exc)
        return {}


def article_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))


def every_name(taxonomy: Dict[str, dict]) -> List[str]:
    """The names the page can put in front of somebody: the classes, and the ranks
    above them that its tree is built out of."""
    names = set(taxonomy)
    for entry in taxonomy.values():
        names.update(str(entry[rank]) for rank in RANKS if entry.get(rank))
    return sorted(names)


def _ask(titles: List[str]) -> Dict[str, str]:
    """One request: which of these titles exist, after following redirects."""
    query = urllib.parse.urlencode({
        "action": "query", "format": "json", "redirects": "1", "prop": "info",
        "titles": "|".join(titles)})
    request = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": AGENT})
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                answer = json.loads(response.read().decode(errors="replace"))
            break
        except Exception as exc:
            if attempt == ATTEMPTS - 1:
                logger.warning("Wikipedia would not answer about %d names: %s",
                               len(titles), exc)
                return {}
            time.sleep(2.0 * (attempt + 1))
    found = answer.get("query") or {}
    # A redirect is the useful part of the answer: it is how a Latin name becomes
    # the word people use for the animal.
    redirects = {r["from"]: r["to"] for r in found.get("redirects", [])}
    # ...and normalisation is the API tidying a title before it looks it up.
    normal = {n["from"]: n["to"] for n in found.get("normalized", [])}
    real = {page["title"] for page in (found.get("pages") or {}).values()
            if "missing" not in page and page.get("ns") == 0}
    articles = {}
    for name in titles:
        title = normal.get(name, name)
        title = redirects.get(title, title)
        if title in real:
            articles[name] = title
    return articles


def fetch_all(names: Iterable[str]) -> Dict[str, str]:
    names = list(names)
    articles: Dict[str, str] = {}
    for first in range(0, len(names), AT_ONCE):
        batch = names[first:first + AT_ONCE]
        articles.update(_ask(batch))
        print(f"  {min(first + AT_ONCE, len(names))}/{len(names)}", flush=True)
    return articles


def main(argv=None) -> int:
    from pixel_patrol_deepsea.fetch_taxonomy import load_taxonomy

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--names", nargs="*", default=None,
                        help="ask about these instead of everything the taxonomy knows")
    args = parser.parse_args(argv)

    taxonomy = load_taxonomy()
    names = args.names or every_name(taxonomy)
    if not names:
        print("no taxonomy fetched yet "
              "(python -m pixel_patrol_deepsea.fetch_taxonomy)")
        return 1
    print(f"asking Wikipedia about {len(names)} names", flush=True)
    articles = fetch_all(names)
    where = wikipedia_path()
    # Merged rather than replaced: a run over a handful of names should not throw
    # away the answers about the rest.
    articles = {**load_articles(), **articles} if args.names else articles
    where.write_text(json.dumps(articles, indent=0, sort_keys=True))
    print(f"\n{len(articles)} of {len(names)} names have an article -> {where} "
          f"({where.stat().st_size / 1024:.0f} KB)")
    missing = [n for n in names if n not in articles]
    if missing:
        print(f"\nno article, so no link is offered for: {', '.join(missing[:40])}"
              + (f" ... and {len(missing) - 40} more" if len(missing) > 40 else ""))
    print("\nArticle titles from Wikipedia (en.wikipedia.org), CC BY-SA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
