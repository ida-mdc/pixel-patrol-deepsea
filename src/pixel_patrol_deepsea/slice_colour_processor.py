"""The mean colour of every time slice, as three numbers rather than a picture.

A colour strip along a recording - which stretch was black water, which was lit
seafloor, where the lights came on - is the fastest way to find your way around an
hour of footage. It was first drawn from `mean_intensity`, and it came out grey,
because the pipeline hands each colour channel to a separate leaf block and
aggregating those leaves averages the three channels into one number. One number
cannot paint three channels, so every column of the strip was a shade of grey.

The second attempt read the cached stills instead, which are in colour and already
in the table. That works, but it ties the strip to `slice_thumbnail`: the combined
report drops the pictures to stay small, and the strip vanished with them.

So the colour is kept as what it always should have been - three floats per slice,
about 24 bytes against 3 KB for a still. They survive being written to a report
with no pictures in it, they need no JPEG decoding in the browser, and they are the
honest form of the measurement: the mean intensity of each channel over the slice.

The trick is the same one the thumbnail processor uses. Each leaf sees one channel
and cannot know which of the three it is; aggregation sees all the leaves *and*
their `dim_c`, so that is where a channel becomes red, green or blue.
"""

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from pixel_patrol_base.core.contracts import ChunkKind
from pixel_patrol_base.core.record import Record
from pixel_patrol_base.core.specs import RecordSpec

logger = logging.getLogger(__name__)

SLICE_RED   = "slice_red"
SLICE_GREEN = "slice_green"
SLICE_BLUE  = "slice_blue"
CHANNELS    = (SLICE_RED, SLICE_GREEN, SLICE_BLUE)


def _channel_means(chunk: np.ndarray, dim_order: str) -> List[float]:
    """The mean of each colour channel in this block, in channel order.

    Usually one number, because a leaf usually holds one channel. Three when the
    loader handed the colour axis over whole, which is what the detector asks for -
    and then this processor is already done and aggregation has nothing to resolve.
    """
    array = np.asarray(chunk)
    if array.size == 0:
        return []
    if "C" not in dim_order or dim_order.index("C") >= array.ndim:
        return [float(np.nanmean(array))]
    axis = dim_order.index("C")
    moved = np.moveaxis(array, axis, 0)
    return [float(np.nanmean(plane)) for plane in moved]


def _spread(values: List[float]) -> Dict[str, float]:
    """Channel means laid out over red, green and blue.

    Monochrome footage, and single-channel leaves that do not know their own place
    yet, put the one value in all three - so a grey recording draws a grey strip
    rather than a red one, and aggregation still has something to pick from.
    """
    if not values:
        return {}
    if len(values) >= 3:
        return dict(zip(CHANNELS, values[:3]))
    only = float(np.mean(values))
    return {name: only for name in CHANNELS}


def _pick(rows: List[Dict], name: str) -> Optional[float]:
    """This channel's mean, out of the rows of whatever sits under this slice.

    The rows are the leaves of one slice, each with its `dim_c`, so red is the row
    from channel zero. Two cases are not that and both matter: a single row already
    holding all three channels (nothing to pick - it is the answer), and footage
    with no channel axis at all (every channel is the same grey).
    """
    known = [row for row in rows if row.get(name) is not None]
    if not known:
        return None
    if len(known) == 1:
        return float(known[0][name])
    wanted = CHANNELS.index(name)
    for row in known:
        if row.get("dim_c") == wanted:
            return float(row[name])
    # More leaves than channels, or channels that never say which they are: the
    # mean is wrong in hue but right in brightness, which beats a hole in the strip.
    return float(np.mean([float(row[name]) for row in known]))


class SliceColourProcessor:
    """Writes the mean of each colour channel, per slice."""

    NAME        = "slice-colour"
    DESCRIPTION = "Records the mean intensity of each colour channel per slice, so a report can draw a recording's colour along its length without carrying any frames."
    CHUNK_KIND  = ChunkKind.LEAF
    INPUT       = RecordSpec(axes={"X", "Y"}, kinds={"intensity"})
    OUTPUT      = "features"
    OUTPUT_SCHEMA: Dict[str, Any] = {name: float for name in CHANNELS}
    OUTPUT_SCHEMA_DESCRIPTIONS: Dict[str, str] = {
        SLICE_RED:   "Mean intensity of the red channel over this slice.",
        SLICE_GREEN: "Mean intensity of the green channel over this slice.",
        SLICE_BLUE:  "Mean intensity of the blue channel over this slice.",
    }

    def run_chunk(self, record: Record) -> Dict:
        chunk = record.data.compute() if hasattr(record.data, "compute") else record.data
        try:
            return _spread(_channel_means(chunk, record.dim_order))
        except Exception as exc:
            logger.warning("slice-colour: %s", exc)
            return {}

    def get_aggregation(self, name: str):
        if name not in CHANNELS:
            return None
        return lambda rows, _dims: _pick(rows, name)
