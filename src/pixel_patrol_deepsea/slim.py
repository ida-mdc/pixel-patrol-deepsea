"""Cut a report down to the pictures the page actually reads.

    python -m pixel_patrol_deepsea.collect slim parquet/

Measured on this collection, 98% of a report is pictures and every number in it -
every metric, every verdict, every box, class and confidence - is the other 2%.
GOA2004 is 3.38 GB of which 0.06 GB is what anybody queries. That weight is the
difference between a report duckdb-wasm opens and one that fails with "Array
buffer allocation failed", so it is worth knowing exactly what the pictures are
for. Three of them turn out not to be worth their bytes:

    detection_crop    a byte-for-byte duplicate. It is the most confident
                      animal's crop, and that same crop is already inside the
                      `detections` JSON beside it - checked over 200 slices
                      holding more than one animal, 200 of 200. 1.94 GB.

    slice_thumbnail   the whole frame at 128x72, one per slice. On the 90% of
                      slices where an animal was named the gallery never shows
                      it: the crop is a better picture of the same moment and
                      the code prefers it every time. It is the only picture on
                      the other 9%, which are the stretches where nothing was
                      named - and a gallery of those is not what this collection
                      is for. 1.92 GB.

    the crops' format they are JPEG, and JPEG is the wrong codec for a picture
                      of this size. Re-encoded as WebP at the same pixels they
                      are 71% of the bytes at q75, 64% at q65, measured over 800
                      real crops.

Their pixels are not worth touching: `crop_of` cuts the box plus a 60% margin so
the animal is recognisable, and the result is stored exactly as cut - over 800
crops the stored width was the cut width 93% of the time, median ratio 1.00.
There is no interpolation in there to reclaim, and resizing towards the box would
throw away the margin that makes a two-centimetre animal identifiable.

Every animal keeps its own crop, at its own size. What goes is a duplicate, a
frame nobody looks at, and JPEG.

**This cannot be undone from the file.** The crops are re-encoded and the frames
are dropped, and the footage they came from is an archive's, not ours. Run it on
a copy first if the collection is one you cannot rebuild.
"""

import argparse
import base64
import io
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Columns that carry a picture the page has a better copy of.
DROP = ("slice_thumbnail", "detection_crop")
# What the crops are re-encoded as. Measured over 800 real crops at their own
# size: q80 is 83% of the stored JPEG, q75 is 71%, q65 is 64%.
CROP_FORMAT = "WEBP"
CROP_QUALITY = 75
# A WebP file begins "RIFF", which is this once base64 has had it.
WEBP_IN_BASE64 = "UklGR"
READ_ROWS = 64
WRITE_BYTES = 48_000_000


def slim(report: Path) -> int:
    """Rewrite one report without its spare pictures. Returns crops re-encoded."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    source = pq.ParquetFile(report, pre_buffer=False)
    have = list(source.schema_arrow.names)
    keep = [name for name in have if name not in DROP]
    if keep == have and "detections" not in have:
        return 0
    schema = pa.schema([source.schema_arrow.field(name) for name in keep],
                       metadata=source.schema_arrow.metadata)
    beside = report.with_name(report.name + ".slimming")
    done = 0
    buffered, held = [], 0
    try:
        with pq.ParquetWriter(beside, schema) as writer:
            for batch in source.iter_batches(batch_size=READ_ROWS, columns=keep):
                table = pa.Table.from_arrays(
                    [batch.column(name) for name in keep], schema=schema)
                if "detections" in keep:
                    table, count = _smaller_crops(table, schema)
                    done += count
                buffered.append(table)
                held += table.nbytes
                if held >= WRITE_BYTES:
                    writer.write_table(pa.concat_tables(buffered))
                    buffered, held = [], 0
            if buffered:
                writer.write_table(pa.concat_tables(buffered))
    except BaseException:
        beside.unlink(missing_ok=True)
        raise
    beside.replace(report)
    return done


def _smaller_crops(table, schema):
    """One batch with every crop written at the size it was seen at."""
    import pyarrow as pa

    column = table.column("detections").to_pylist()
    done = 0
    rewritten = []
    for raw in column:
        if not raw:
            rewritten.append(raw)
            continue
        try:
            animals = json.loads(raw)
        except Exception:                   # a row that is not JSON stays as it is
            rewritten.append(raw)
            continue
        changed = False
        for animal in animals:
            crop = _recoded(animal.get("crop"))
            if crop is not None:
                animal["crop"], changed = crop, True
                done += 1
        rewritten.append(json.dumps(animals, separators=(",", ":")) if changed else raw)
    at = schema.get_field_index("detections")
    arrays = [table.column(i) if i != at else pa.array(rewritten, type=schema.field(at).type)
              for i in range(len(schema))]
    return pa.Table.from_arrays(arrays, schema=schema), done


def _recoded(crop: Optional[str]) -> Optional[str]:
    """The same picture, same pixels, in a codec that suits its size.

    Left alone if it is already WebP, so that running this twice costs a report
    nothing but the reading - and does not put it through a second lossy pass.
    """
    from PIL import Image

    if not crop or crop.startswith(WEBP_IN_BASE64):
        return None
    try:
        picture = Image.open(io.BytesIO(base64.b64decode(crop))).convert("RGB")
    except Exception:
        return None
    held = io.BytesIO()
    picture.save(held, CROP_FORMAT, quality=CROP_QUALITY)
    return base64.b64encode(held.getvalue()).decode()


def slim_reports(target: Path) -> int:
    from pixel_patrol_deepsea.collect import _reports_under

    reports = _reports_under(target)
    if not reports:
        print(f"no reports under {target}", file=sys.stderr)
        return 1
    was = now = crops = 0
    for report in reports:
        before = report.stat().st_size
        try:
            done = slim(report)
        except Exception as exc:
            print(f"{report}: {exc}", file=sys.stderr)
            continue
        after = report.stat().st_size
        was, now, crops = was + before, now + after, crops + done
        print(f"{report.name}: {before/1e9:.2f} GB -> {after/1e9:.2f} GB, "
              f"{done:,} crops rewritten")
    print(f"{len(reports)} reports, {was/1e9:.2f} GB -> {now/1e9:.2f} GB, "
          f"{crops:,} crops rewritten")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path,
                        help="a parquet, or a collection root to walk")
    return slim_reports(parser.parse_args(argv).target)


if __name__ == "__main__":
    sys.exit(main())
