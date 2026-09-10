"""Kernels that describe the colour *distribution* of a frame, not its average.

The mean colour of a slice is three numbers and it hides the thing worth seeing. A
frame of white rubble beside orange coral averages to a dull tan, and so does a
frame of uniform tan sediment; a bleached colony and a pigmented one under the same
lamp average closer together than they look. What separates them is the shape of
the distribution - how much of the frame carries strong colour at all, and which
hues - so that is what is measured here and written per slice, once, so the
question can be asked of the report afterwards rather than of the footage again.

Two honest warnings, because a number that travels into a plot outlives its
caveats:

**Everything below the photic zone is lit by the vehicle.** Water absorbs red
within metres, so the colour of a subject depends on how far the lamps were from
it and how they were set that dive. A drop in saturation along a recording can be
the animal changing or the pilot backing off, and only the second is common.
Compare within a dive before comparing between them.

**Saturation is the axis bleaching moves along, not hue.** A bleached or dead coral
is bright and grey-white: it keeps its brightness and loses its chroma. That shows
up as weight moving into the lowest saturation bins while `value` stays high -
which is exactly what a pale sediment background does too, so the measure separates
candidates for a person to look at rather than diagnosing anything.

Hue is weighted by saturation throughout. The hue of a grey pixel is not a weak
colour, it is arithmetic noise: at zero chroma the formula divides by nothing and
the answer swings across the whole circle, so counting those votes fills every bin
with the same fog.
"""

from typing import Dict, Tuple

import numpy as np

# Twelve hues of thirty degrees and eight steps of saturation. Enough shape to see
# a population move and few enough columns to read as a table - the alternative,
# one 256-bin histogram per channel, is a picture of a picture.
HUE_BINS = 12
SATURATION_BINS = 8
# Below this a pixel has no colour to speak of and its hue is noise; it is still
# counted in the saturation histogram, where "almost grey" is the reading that
# matters.
COLOUR_FLOOR = 0.12
# Below this a pixel is too dark for either to mean anything - the unlit water at
# the edge of the lamp cone, which is most of the frame in midwater footage.
DARK_FLOOR = 0.08


def _as_float(frame: np.ndarray) -> np.ndarray:
    """RGB in 0..1, whatever integer or float range it arrived in."""
    array = np.asarray(frame)
    if np.issubdtype(array.dtype, np.integer):
        return array.astype(np.float32) / float(np.iinfo(array.dtype).max)
    array = array.astype(np.float32)
    peak = float(np.nanmax(array)) if array.size else 1.0
    return array / peak if peak > 1.0 else array


def hue_saturation_value(frame: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hue in degrees, saturation and value in 0..1, for an (Y, X, 3) frame.

    Written out rather than taken from a colour library so the package keeps its
    dependency list, and because the only part that is subtle - what hue means
    where there is no chroma - is a decision this module wants to make itself.
    """
    rgb = _as_float(frame)
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    value = np.max(rgb, axis=-1)
    low = np.min(rgb, axis=-1)
    chroma = value - low
    saturation = np.where(value > 0, chroma / np.maximum(value, 1e-6), 0.0)
    safe = np.maximum(chroma, 1e-6)
    hue = np.select(
        [chroma <= 0, value == red, value == green],
        [np.zeros_like(value),
         ((green - blue) / safe) % 6.0,
         ((blue - red) / safe) + 2.0],
        default=((red - green) / safe) + 4.0) * 60.0
    return hue, saturation, value


def colourfulness(frame: np.ndarray) -> float:
    """Hasler and Susstrunk's measure, on the usual 0-255 scale.

    One number for "how colourful", and the standard one, so a reader can compare
    it with values quoted anywhere else. It is the spread of the two colour-opponent
    axes plus a fraction of how far their mean sits from grey, which is high both
    for a frame holding many different strong colours and for one holding a single
    saturated one.
    """
    rgb = _as_float(frame) * 255.0
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    against = red - green
    across = 0.5 * (red + green) - blue
    spread = np.sqrt(np.var(against) + np.var(across))
    distance = np.hypot(np.mean(against), np.mean(across))
    return float(spread + 0.3 * distance)


def colour_spread(frames: np.ndarray) -> Dict[str, float]:
    """The colour distribution of a stack of (Y, X, 3) frames, as flat numbers.

    Histograms are shares, so they are comparable between a slice sampled at four
    frames and one sampled at one, and between footage of different sizes. The
    denominators differ on purpose and each is named: hue is a share of the
    *colourful* weight, saturation a share of the *lit* pixels.
    """
    stack = np.asarray(frames)
    if stack.ndim != 4 or stack.shape[-1] < 3:
        return {}
    hue_weight = np.zeros(HUE_BINS, dtype=np.float64)
    saturation_count = np.zeros(SATURATION_BINS, dtype=np.float64)
    lit_total = 0.0
    colourful_total = 0.0
    saturation_sum = 0.0
    scores = []
    for frame in stack:
        hue, saturation, value = hue_saturation_value(frame[..., :3])
        lit = value >= DARK_FLOOR
        if not np.any(lit):
            continue
        scores.append(colourfulness(frame[..., :3]))
        lit_saturation = saturation[lit]
        lit_total += lit_saturation.size
        saturation_sum += float(np.sum(lit_saturation))
        edges = np.clip((lit_saturation * SATURATION_BINS).astype(np.int64),
                        0, SATURATION_BINS - 1)
        saturation_count += np.bincount(edges, minlength=SATURATION_BINS)
        colourful = lit & (saturation >= COLOUR_FLOOR)
        if not np.any(colourful):
            continue
        weights = saturation[colourful]
        bins = np.clip((hue[colourful] / (360.0 / HUE_BINS)).astype(np.int64),
                       0, HUE_BINS - 1)
        hue_weight += np.bincount(bins, weights=weights, minlength=HUE_BINS)
        colourful_total += float(np.sum(weights))
    if lit_total <= 0:
        return {}
    out = {
        "colourfulness": float(np.mean(scores)) if scores else 0.0,
        "saturation_mean": saturation_sum / lit_total,
        "colourful_fraction": float(np.sum(saturation_count[
            int(COLOUR_FLOOR * SATURATION_BINS) + 1:]) / lit_total),
    }
    for index in range(HUE_BINS):
        out[f"hue_{index:02d}"] = (hue_weight[index] / colourful_total
                                   if colourful_total > 0 else 0.0)
    for index in range(SATURATION_BINS):
        out[f"saturation_{index:02d}"] = saturation_count[index] / lit_total
    return out
