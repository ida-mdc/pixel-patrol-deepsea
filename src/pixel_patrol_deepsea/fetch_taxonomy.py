"""Resolve the detector's class names to real taxonomic lineages, once.

    python -m pixel_patrol_deepsea.fetch_taxonomy

A detector's vocabulary is a flat list of names, and a flat list of names is the
least useful way to look at what a report found. Sixty entries reading
`Actiniaria`, `Isididae`, `Antimora microlepis`, `Calyptogena` tell you nothing
about the fact that two of them are cnidarians, one is a fish and one is a clam -
which is the first thing anybody wants to know about a dive.

The names are Linnaean, so the hierarchy exists; it is just not in the model. This
fetches it from the World Register of Marine Species, which is the authority for
marine taxa and answers over a plain REST API, and writes it beside the module as
`taxonomy.json`.

It is a *fetch* rather than a lookup at run time, deliberately. The vocabulary is
fixed - it is a property of the checkpoint, not of any report - so resolving it
once means the viewer and the collection page need no network at all, which is the
difference between a report that opens anywhere and one that opens online.

The names are not all Linnaean. FathomNet uses a few composite labels for
creatures that cannot be told apart on sight, `Funiculina-Balticina complex` being
the one that matters here, and a few equipment classes that are not animals. Those
are resolved as far as they can be and recorded as unplaced rather than dropped:
a report should not silently lose a detection because a name was awkward.
"""

import argparse
import json
import logging
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

WORMS = "https://www.marinespecies.org/rest/AphiaRecordsByName/{name}?like=false&marine_only=false"
TIMEOUT = 30
# Polite rather than fast: this is somebody else's free service and it is run once.
# Six at a time got 177 of 499 names and then a wall of failures starting abruptly
# at the letter D - which is not what a taxonomic register looks like, it is what
# rate limiting looks like. Two at a time, with a backoff, resolves the rest.
AT_ONCE = 2
ATTEMPTS = 4

# The ranks worth keeping, coarsest first. A sunburst with every intermediate rank
# in it is unreadable, and these are the ones a reader names things by.
RANKS = ("kingdom", "phylum", "class", "order", "family", "genus")


def taxonomy_path() -> Path:
    return Path(__file__).parent / "taxonomy.json"


def load_taxonomy() -> Dict[str, dict]:
    """The lineage of every name the detector knows, or an empty map if unfetched."""
    where = taxonomy_path()
    if not where.is_file():
        return {}
    try:
        return json.loads(where.read_text())
    except Exception as exc:
        logger.warning("cannot read %s: %s", where, exc)
        return {}


def lineage_of(name: str, table: Optional[Dict[str, dict]] = None) -> List[str]:
    """The ranks above a name, coarsest first, and then the name itself.

    An unplaced name still gets a lineage - `["Unplaced", name]` - because the
    alternative is a tree that quietly holds fewer animals than the report does.
    """
    entry = (table if table is not None else load_taxonomy()).get(name)
    if not entry:
        return ["Unplaced", name]
    above = [entry[rank] for rank in RANKS if entry.get(rank)]
    return above + ([name] if not above or above[-1] != name else [])


# ── asking WoRMS ──────────────────────────────────────────────────────────────

def candidates(name: str) -> List[str]:
    """Ways of asking about a name, from the most specific to the most hopeful.

    `Funiculina-Balticina complex` is two genera of sea pen that cannot be told
    apart in a photograph, so no register has it. Asking about `Funiculina` gets
    the family and order right, which is all a tree needs, and is a better answer
    than none.
    """
    tried = [name]
    without_note = re.sub(r"\s*\b(complex|spp?\.?|sp\.)\b\s*$", "", name).strip()
    if without_note and without_note != name:
        tried.append(without_note)
    if "-" in without_note:
        tried.append(without_note.split("-")[0].strip())
    first = without_note.split()[0] if without_note.split() else ""
    if first and first not in tried:
        tried.append(first)
    return tried


def ask_worms(name: str) -> Optional[dict]:
    """One question to WoRMS, retried, because a refusal is not an answer.

    A 429 or a dropped connection means "ask again later", and treating it as "no
    such taxon" is how a third of the vocabulary ended up unplaced with no sign
    that anything had gone wrong. A genuine miss is a 204 with an empty body and
    comes back the same way every time, so retrying costs nothing on those.
    """
    import time

    url = WORMS.format(name=urllib.parse.quote(name))
    found = None
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
                if response.status == 204:
                    return None                     # no such taxon; a real answer
                if response.status != 200:
                    raise OSError(f"HTTP {response.status}")
                found = json.loads(response.read().decode(errors="replace"))
            break
        except Exception:
            if attempt == ATTEMPTS - 1:
                logger.warning("WoRMS would not answer about %r", name)
                return None
            time.sleep(1.5 * (attempt + 1))
    if not isinstance(found, list) or not found:
        return None
    # Accepted names first: a synonym's lineage is the same tree by a different
    # door, but the accepted record is the one to name a branch after.
    accepted = [r for r in found if (r.get("status") or "") == "accepted"]
    return (accepted or found)[0]


def resolve(name: str) -> dict:
    """One class name's lineage, and how it was arrived at."""
    for attempt in candidates(name):
        record = ask_worms(attempt)
        if not record:
            continue
        entry = {rank: record.get(rank) for rank in RANKS if record.get(rank)}
        entry["rank"] = record.get("rank") or ""
        entry["matched"] = attempt
        entry["aphia_id"] = record.get("AphiaID")
        if record.get("rank") == "Species" and record.get("scientificname"):
            entry["species"] = record["scientificname"]
        return entry
    return {}


def fetch_all(names: List[str], already: Dict[str, dict]) -> Dict[str, dict]:
    wanted = [n for n in names if n not in already]
    print(f"{len(already)} already known, {len(wanted)} to resolve", flush=True)
    table = dict(already)
    with ThreadPoolExecutor(AT_ONCE) as pool:
        for done, (name, entry) in enumerate(
                zip(wanted, pool.map(resolve, wanted)), 1):
            if entry:
                table[name] = entry
            if done % 25 == 0:
                print(f"  {done}/{len(wanted)}", flush=True)
    return table


def detector_classes() -> List[str]:
    from pixel_patrol_deepsea import detector
    return list(detector.load_detector()[1])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--names", nargs="*", default=None,
                        help="resolve these instead of the detector's whole vocabulary")
    parser.add_argument("--refresh", action="store_true",
                        help="ask again about names already in the file")
    args = parser.parse_args(argv)

    names = args.names or detector_classes()
    already = {} if args.refresh else load_taxonomy()
    table = fetch_all(sorted(set(names)), already)

    placed = sum(1 for n in names if n in table)
    where = taxonomy_path()
    where.write_text(json.dumps(table, indent=0, sort_keys=True))
    print(f"\n{placed} of {len(names)} names placed -> {where} "
          f"({where.stat().st_size / 1024:.0f} KB)")
    missing = [n for n in names if n not in table]
    if missing:
        print(f"\nunplaced, and shown as such rather than dropped: {', '.join(missing)}")
    print("\nTaxonomy from the World Register of Marine Species (marinespecies.org),")
    print("CC-BY. Cite WoRMS where the tree is shown.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
