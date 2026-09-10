"""The colour distribution of every slice, kept so it can be asked questions later.

`slice-colour` writes the mean of each channel, which is what a colour strip along
a recording needs and all it needs. This writes the shape of the distribution
instead: how much of the frame carries colour at all, how strongly, and in which
hues. Those are the numbers a question like "did this reef lose its colour between
2019 and 2021" is actually made of, and they cannot be recovered afterwards from a
mean - or from a 3 KB thumbnail - so they are computed while the pixels are in
hand and written into the parquet with everything else.

Twenty-three floats a slice, about a hundred bytes against three kilobytes for the
cached still, and no decoding to read them: a plot over a whole expedition is a
column scan over the report rather than a second pass over the footage.

See `colour_numpy_metrics` for what the bins mean and for the two things that will
mislead anyone plotting this without reading them - that the lighting is the
vehicle's own, and that bleaching moves saturation rather than hue.
"""

import logging
from typing import Any, Dict, Tuple

import numpy as np

from pixel_patrol_base.core.contracts import ChunkKind
from pixel_patrol_base.core.record import Record
from pixel_patrol_base.core.specs import RecordSpec
from pixel_patrol_base.plugins.processors.raster_processor import (
    RasterMetricSpec,
    _weighted_mean_agg,
)
from pixel_patrol_deepsea.colour_numpy_metrics import (
    COLOUR_FLOOR, DARK_FLOOR, HUE_BINS, SATURATION_BINS, colour_spread,
)

logger = logging.getLogger(__name__)

COLOURFULNESS      = "colourfulness"
SATURATION_MEAN    = "colour_saturation_mean"
COLOURFUL_FRACTION = "colour_saturated_fraction"
HUE = tuple(f"colour_hue_{i:02d}" for i in range(HUE_BINS))
SATURATION = tuple(f"colour_saturation_{i:02d}" for i in range(SATURATION_BINS))

# Four frames of a slice, which at one-second slices is four looks a second. The
# distribution of a second of footage does not move much within it, and this is
# meant to cost a fraction of what the detector on the same slice costs.
FRAMES_SAMPLED = 4
# Enough pixels to fill the bins and no more. A 640x360 proxy is under this and is
# read whole; HD is read every second or third pixel, which changes a share by less
# than the bin width.
PIXELS_PER_FRAME = 200_000

_DEGREES = 360 // HUE_BINS


def _described() -> Tuple[RasterMetricSpec, ...]:
    """Every column, with a description that says what its denominator is."""
    specs = [
        RasterMetricSpec(
            name=COLOURFULNESS, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description=(
                "Hasler and Susstrunk's colourfulness on the usual 0-255 scale, "
                "averaged over the frames sampled from this slice. High for a frame "
                "holding strong colour, whether one colour or many. Below the photic "
                "zone this is a joint measurement of the subject and of the vehicle's "
                "lamps: water absorbs red within metres, so backing the ROV off a "
                "colony lowers this without anything having changed. Compare within a "
                "dive before comparing between dives."),
        ),
        RasterMetricSpec(
            name=SATURATION_MEAN, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description=(
                "Mean HSV saturation over the pixels bright enough to have a colour "
                f"(value >= {DARK_FLOOR}). The unlit water outside the lamp cone is "
                "excluded rather than counted as grey, because in midwater footage it "
                "is most of the frame and would swamp the subject."),
        ),
        RasterMetricSpec(
            name=COLOURFUL_FRACTION, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description=(
                f"Share of those lit pixels whose saturation is above {COLOUR_FLOOR}: "
                "how much of what the lamps reached carries colour at all. This is the "
                "axis bleaching moves along - a bleached or dead colony stays bright and "
                "loses its chroma - though pale sediment does the same thing, so read it "
                "as a shortlist rather than a diagnosis."),
        ),
    ]
    specs += [
        RasterMetricSpec(
            name=name, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description=(
                f"Share of this slice's colour weight in hues {index * _DEGREES} to "
                f"{(index + 1) * _DEGREES} degrees ({_hue_name(index)}). Weighted by "
                "saturation, because the hue of a near-grey pixel is arithmetic noise "
                "that would otherwise fill every bin evenly. The twelve bins sum to 1 "
                "where any colour was found and to 0 where none was."),
        ) for index, name in enumerate(HUE)
    ]
    specs += [
        RasterMetricSpec(
            name=name, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description=(
                f"Share of lit pixels whose saturation falls between "
                f"{index / SATURATION_BINS:.3f} and {(index + 1) / SATURATION_BINS:.3f}. "
                "The eight bins sum to 1. The lowest bin is the grey end - white rubble, "
                "bleached skeleton, backscatter - and the highest is strong colour."),
        ) for index, name in enumerate(SATURATION)
    ]
    return tuple(specs)


def _hue_name(index: int) -> str:
    """Roughly what a bin looks like, for a reader who does not think in degrees."""
    names = ("red", "orange", "yellow", "yellow-green", "green", "green-cyan",
             "cyan", "cyan-blue", "blue", "violet", "magenta", "pink-red")
    return names[index] if index < len(names) else "?"


class SliceColourSpreadProcessor:
    """Writes the hue and saturation distribution of each slice."""

    NAME        = "slice-colour-spread"
    DESCRIPTION = ("Records the colour distribution of each slice - a twelve-bin hue "
                   "histogram, an eight-bin saturation histogram and a colourfulness "
                   "score - so how colourful a recording is, and in what way, can be "
                   "plotted from the report without reading the footage again.")
    CHUNK_KIND  = ChunkKind.LEAF
    INPUT       = RecordSpec(axes={"X", "Y"}, kinds={"intensity"}, capabilities={"spatial-2d"})
    OUTPUT      = "features"

    METRICS: Tuple[RasterMetricSpec, ...] = _described()
    OUTPUT_SCHEMA: Dict[str, Any] = {m.name: m.data_type for m in METRICS}
    OUTPUT_SCHEMA_DESCRIPTIONS: Dict[str, str] = {m.name: m.description for m in METRICS}

    def run_chunk(self, record: Record) -> Dict:
        chunk = record.data.compute() if hasattr(record.data, "compute") else np.asarray(record.data)
        try:
            stack = _frames_with_colour(chunk, record.dim_order)
            if stack is None:
                return {}
            found = colour_spread(stack)
        except Exception as exc:
            logger.warning("slice-colour-spread: %s", exc)
            return {}
        if not found:
            return {}
        row = {COLOURFULNESS: float(found["colourfulness"]),
               SATURATION_MEAN: float(found["saturation_mean"]),
               COLOURFUL_FRACTION: float(found["colourful_fraction"])}
        row.update({name: float(found[f"hue_{i:02d}"]) for i, name in enumerate(HUE)})
        row.update({name: float(found[f"saturation_{i:02d}"])
                    for i, name in enumerate(SATURATION)})
        return row

    def get_aggregation(self, name: str):
        spec = next((s for s in self.METRICS if s.name == name), None)
        if spec is None:
            return None
        return lambda rows, _dims: spec.aggregate_rows(spec, rows)


def _frames_with_colour(chunk: np.ndarray, dim_order: str):
    """The slice as a few (Y, X, C) frames, or nothing if it has no colour axis.

    Monochrome footage has no distribution to describe - every pixel is its own
    grey - and saying so with an absent column is more honest than writing a
    saturation of zero into a report that a reader will average.
    """
    if "C" not in dim_order:
        return None
    y_ax, x_ax, c_ax = (dim_order.index(axis) for axis in "YXC")
    lead = [i for i in range(chunk.ndim) if i not in (y_ax, x_ax, c_ax)]
    moved = chunk.transpose(lead + [y_ax, x_ax, c_ax])
    stack = moved.reshape(-1, *moved.shape[-3:])
    if stack.shape[0] == 0 or stack.shape[-1] < 3 or stack.shape[-2] < 8:
        return None
    picks = np.unique(np.linspace(0, stack.shape[0] - 1,
                                  min(FRAMES_SAMPLED, stack.shape[0])).astype(int))
    frames = stack[picks]
    step = max(1, int(np.sqrt((frames.shape[1] * frames.shape[2]) / PIXELS_PER_FRAME)))
    return frames[:, ::step, ::step, :]

