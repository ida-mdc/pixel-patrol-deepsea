"""The collection's own colours, as a picture.

    python -m pixel_patrol_deepsea.banner collection/

Every recording that has been read, in the order it was filmed, as one vertical
stripe: a band of colour per slice, top to bottom, which is that recording's own
mean colour as it went down and along. Side by side they are the collection - a few
hundred dives from 2004 to 2026, in the colours the vehicles' lamps and a kilometre
of water left them.

It is a truthful picture and not a decoration. The blue-green columns are the
midwater and the lit seabed; the near-black ones are transits and night; the odd
warm one is a vehicle's own hardware filling the frame, or a bright rock close to
the lights. Nothing is stretched to fill the palette - the values are the means the
processors already measured, which is why the whole thing is so nearly cyan.

Written once, by `collect site`, into `assets/colours.png`, because computing it in
the page would mean reading six gigabytes of parquet in a browser.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

ASSET = "assets/colours.png"
# How tall a stripe is. Recordings hold a few hundred slices each, so this is a mild
# downsample of the ones with more and a stretch of the few with fewer.
HEIGHT = 240
# How wide one recording is. Wide enough to be a stripe rather than a line, narrow
# enough that three hundred of them are a band and not a mural.
STRIPE = 4


def build(root: Path, output: Optional[Path] = None) -> Optional[Path]:
    """Write the banner for a collection, and return where it went."""
    import numpy as np
    from PIL import Image

    columns = _columns(root)
    if not columns:
        logger.info("banner: no colours in this collection's reports")
        return None
    band = np.zeros((HEIGHT, len(columns) * STRIPE, 3), dtype=np.uint8)
    for at, colours in enumerate(columns):
        band[:, at * STRIPE:(at + 1) * STRIPE] = colours[:, None, :]
    output = output or root / ASSET
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(band).save(output, optimize=True)
    logger.info("banner: %d recordings -> %s", len(columns), output)
    return output


def _columns(root: Path) -> List:
    """One column of colour per recording, oldest first.

    Ordered by when the footage was taken rather than by expedition, so the band
    reads left to right as two decades: the 2004 Gulf of Alaska tapes at one end and
    last year's Papahānaumokuākea dives at the other.
    """
    import numpy as np
    import polars as pl

    reports = sorted(p for p in (root / "parquet").glob("*.parquet")
                     if not p.stem.startswith("_"))
    taken = []
    for report in reports:
        try:
            columns = pl.read_parquet_schema(report)
            wanted = [c for c in ("name", "dim_t", "recorded_at",
                                  "slice_red", "slice_green", "slice_blue")
                      if c in columns]
            if not {"name", "slice_red"} <= set(wanted):
                continue
            table = pl.read_parquet(report, columns=wanted)
        except Exception as exc:
            logger.warning("banner: cannot read %s: %s", report.name, exc)
            continue
        # Slice rows only: the aggregate row for a whole recording has no `dim_t`
        # and its colour is an average of averages.
        if "dim_t" in table.columns:
            table = table.filter(pl.col("dim_t").is_not_null())
        table = table.drop_nulls(subset=["slice_red", "slice_green", "slice_blue"])
        for (name,), rows in table.group_by(["name"], maintain_order=True):
            rows = rows.sort("dim_t" if "dim_t" in rows.columns else "name")
            colours = np.stack([rows["slice_red"].to_numpy(),
                                rows["slice_green"].to_numpy(),
                                rows["slice_blue"].to_numpy()], axis=1)
            when = _when(rows, name)
            taken.append((when, str(name), _stretch(colours)))
    taken.sort(key=lambda one: (one[0], one[1]))
    return [colours for _when, _name, colours in taken]


def _when(rows, name: str) -> str:
    """When this recording was filmed, for sorting the band by time.

    The clock the location pass wrote if it is there, and the file name if it is
    not - the archives put the UTC stamp in the name, so sorting on it is the same
    answer by a different route.
    """
    if "recorded_at" in rows.columns:
        stamps = rows["recorded_at"].drop_nulls().to_list()
        if stamps:
            return str(min(stamps))
    return str(name)


def _stretch(colours):
    """One recording's colours, as exactly HEIGHT rows of it.

    Averaged into bands rather than sampled, so a long recording keeps every slice
    in it - a stripe is the whole dive, not a hundred moments from it.
    """
    import numpy as np

    if not len(colours):
        return np.zeros((HEIGHT, 3), dtype=np.uint8)
    edges = np.linspace(0, len(colours), HEIGHT + 1).astype(int)
    rows = np.empty((HEIGHT, 3), dtype=np.float64)
    for band in range(HEIGHT):
        first, last = edges[band], max(edges[band + 1], edges[band] + 1)
        rows[band] = colours[first:last].mean(axis=0)
    return np.clip(rows, 0, 255).astype(np.uint8)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="the collection directory")
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    where = build(args.root, args.output)
    print(where or "nothing to draw")
    return 0 if where else 1


if __name__ == "__main__":
    sys.exit(main())
