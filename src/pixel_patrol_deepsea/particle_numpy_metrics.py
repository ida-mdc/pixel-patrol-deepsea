"""Kernels that count small bright particles in a frame (Y, X at the last two axes).

Every other kernel here reduces a frame to one number about the whole frame. These
count *how many separate small bright things are in it*, which a band-pass filter
plus a size threshold can answer without a trained model.

What that means depends entirely on the scene, and the difference was measured:

- **Midwater**, where animals are small bright objects drifting in dark water, the
  particles are the animals. On MBARI's annotated DeepSea-MOT sequence this reached
  0.85 precision at 0.39 recall. The size threshold is what made it work - a plain
  difference of Gaussians found ~3,600 objects per frame where 48 were annotated,
  1% precision, because marine snow looks just like a small animal to a band-pass.
- **Benthic**, close to a lit seafloor, the same measure inverts. On a NOAA dive
  tape the frame scoring highest (284 particles) was empty distant water full of
  marine snow, and the frame scoring lowest (3) was a gold coral colony filling the
  view. A large, close subject is low-frequency, so a band-pass treats it as
  background. Coarser scales do not rescue it: retuned to 3/12 px the coral still
  scored 0 while the empty water scored 21.

So this is a particle counter, not an animal detector. Read it as animals only in
midwater; elsewhere read it as particulate load, which is its own image-quality
variable.
"""

import math
from typing import Tuple

import numpy as np
from scipy import ndimage

# Fitted on MBARI midwater footage at 1920x1080, then scaled to whatever comes in.
_REFERENCE_PIXELS = 1920 * 1080
_SMALL_SIGMA = 1.5
_LARGE_SIGMA = 5.0
_MIN_AREA = 100.0
_THRESHOLD_MADS = 5.0


def scale_for(height: int, width: int) -> float:
    """Linear scale between this frame and the resolution the defaults were fitted at."""
    return math.sqrt((height * width) / _REFERENCE_PIXELS)


def bright_particle_mask(frame: np.ndarray, scale: float) -> np.ndarray:
    """Pixels that stand out from their surroundings at roughly animal scale.

    The band-pass suppresses both the slowly varying background - the lit cone of
    an ROV lamp, a sediment gradient - and single-pixel sensor noise. The threshold
    is set from the median absolute deviation so it travels between recordings with
    very different exposure.
    """
    band = (ndimage.gaussian_filter(frame, max(0.8, _SMALL_SIGMA * scale))
            - ndimage.gaussian_filter(frame, max(1.6, _LARGE_SIGMA * scale)))
    middle = float(np.median(band))
    deviation = float(np.median(np.abs(band - middle))) or 1.0
    return band > middle + _THRESHOLD_MADS * 1.4826 * deviation


def count_bright_particles(frame: np.ndarray, scale: float) -> Tuple[int, float]:
    """Number of particles above the size floor, and the share of the frame they cover."""
    mask = bright_particle_mask(frame, scale)
    labels, found = ndimage.label(mask)
    if not found:
        return 0, 0.0
    areas = np.asarray(ndimage.sum(mask, labels, range(1, found + 1)))
    big = areas[areas >= _MIN_AREA * scale * scale]
    return int(big.size), float(big.sum() / frame.size)


def particles_per_frame(arr: np.ndarray, sample: int) -> Tuple[float, float]:
    """Mean particle count and area fraction over a sample of the block's frames.

    A block can hold every frame of a second; counting particles costs far more than
    the whole-frame statistics beside it, and consecutive frames of the same second
    show the same particles, so a few evenly spaced frames give the same answer.
    """
    frames = arr.reshape(-1, arr.shape[-2], arr.shape[-1])
    if frames.shape[0] == 0:
        return float("nan"), float("nan")
    picks = np.unique(np.linspace(0, frames.shape[0] - 1, min(sample, frames.shape[0])).astype(int))
    scale = scale_for(arr.shape[-2], arr.shape[-1])
    results = [count_bright_particles(frames[i].astype(np.float32), scale) for i in picks]
    return float(np.mean([c for c, _ in results])), float(np.mean([a for _, a in results]))
