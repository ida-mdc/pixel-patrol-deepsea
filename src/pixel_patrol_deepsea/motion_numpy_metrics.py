"""Find things that move when the camera's own movement is taken out.

A detector can only find animals it was trained on. Background subtraction has no
vocabulary at all: it reports that something here is not where the scene usually is,
which is true of a fish, a squid, and of an animal nobody has described. That is the
whole reason it is here.

The naive version does not work on ROV footage and it is worth saying why, because
the failure is not subtle. On a NOAA dive tape the camera translates at a median of
19 px/s and holds still (under 2 px/s) for 6% of the time, with the longest unbroken
still stretch in five minutes being two seconds. Differencing against a median
background under those conditions returns the whole seabed - every coral branch has
moved - and the fish in the middle of the frame is invisible in the result.

So the camera's own motion is estimated and removed first, by phase correlation,
before anything is subtracted. What survives is what moved independently. Where the
motion is too fast to register, or the scene is too three-dimensional for a single
shift to describe, that is reported rather than guessed at.
"""

from typing import List, Optional, Tuple

import numpy as np

WINDOW_FRAMES = 10        # frames compared against one another at a time
MAX_SHIFT = 60            # px; past this the alignment is not to be trusted
RESIDUAL_LIMIT = 8.0      # typical leftover difference after aligning, in grey levels
DIFFERENCE_FLOOR = 14.0   # a pixel must differ by this much whatever the local noise
NOISE_MULTIPLE = 5.0      # ...and by this many times its own median deviation
MIN_AREA_FRACTION = 3e-3  # smaller blobs are texture shimmer, not animals
MAX_AREA_FRACTION = 0.35  # bigger ones are the scene shifting, not an animal


def camera_shift(a: np.ndarray, b: np.ndarray) -> Tuple[int, int]:
    """Global (dy, dx) between two frames, by phase correlation.

    Signed so that `np.roll(a, (-dy, -dx))` brings `a` into register with `b`.

    Phase correlation rather than tracking features: it is one FFT per frame, it
    does not care that deep-sea footage is dark and low contrast, and the whole
    frame votes so a single moving animal cannot drag the estimate with it.
    """
    fa, fb = np.fft.rfft2(a), np.fft.rfft2(b)
    cross = fa * np.conj(fb)
    cross /= np.abs(cross) + 1e-9
    peak = np.fft.irfft2(cross, s=a.shape)
    y, x = np.unravel_index(int(np.argmax(peak)), peak.shape)
    return (int(y - a.shape[0] if y > a.shape[0] // 2 else y),
            int(x - a.shape[1] if x > a.shape[1] // 2 else x))


def moving_objects(frames: np.ndarray, seconds: float = 1.0,
                   min_area_fraction: float = MIN_AREA_FRACTION
                   ) -> Tuple[float, float, float, Optional[Tuple[int, int, int, int]]]:
    """Return (camera speed in px/s, object count, largest area share, largest box).

    The slice is worked in short windows, because the further apart two frames are
    the further the camera has travelled and the less a single shift describes it.
    The busiest window wins: an animal that crosses the view in one second of a
    three-second slice should not be averaged away by the two quiet seconds.
    """
    stack = np.asarray(frames, dtype=np.float32)
    if stack.ndim != 3 or len(stack) < 4 or min(stack.shape[1:]) < 32:
        return float("nan"), 0.0, 0.0, None

    per_frame = seconds / max(1, len(stack) - 1)
    speeds: List[float] = []
    best = (0.0, 0.0, None)
    for window in _windows(stack, WINDOW_FRAMES):
        aligned, _shifts = _align(window)
        speeds.append(_speed_across(window, per_frame))
        if aligned is None:
            continue
        found = _objects_in(aligned, min_area_fraction)
        if found and found[1] > best[1]:
            best = found
    speed = float(np.median(speeds)) if speeds else float("nan")
    return (speed, *best)


def _speed_across(window: np.ndarray, per_frame: float) -> float:
    """Pixels per second, from where the scene started to where it ended up.

    End to end rather than frame to frame: a single frame's shift is a couple of
    pixels on this footage, which is the resolution of the estimate itself, and
    averaging those errors is noisier than measuring the whole span once.
    """
    # Saturates rather than climbing once the scene moves more than half a frame
    # across the window: phase correlation wraps at that point. Anything that fast
    # has no usable background anyway, and the alignment refuses it separately.
    travelled = float(np.hypot(*camera_shift(window[0], window[-1])))
    return travelled / max(1e-6, (len(window) - 1) * per_frame)


def _windows(stack: np.ndarray, size: int):
    for start in range(0, max(1, len(stack) - size + 1), size):
        chunk = stack[start:start + size]
        if len(chunk) >= 4:
            yield chunk


def _align(window: np.ndarray):
    """Roll every frame onto the middle one; None when the shift is untrustworthy."""
    anchor = len(window) // 2
    shifts = [camera_shift(frame, window[anchor]) for frame in window]
    reach = max(max(abs(dy), abs(dx)) for dy, dx in shifts)
    if reach > MAX_SHIFT:
        return None, shifts
    pad = max(1, reach)
    rolled = [np.roll(frame, (-dy, -dx), axis=(0, 1))[pad:-pad, pad:-pad]
              for frame, (dy, dx) in zip(window, shifts)]
    if min(rolled[0].shape) < 32:
        return None, shifts
    return np.stack(rolled), shifts


def _objects_in(aligned: np.ndarray, min_area_fraction: float):
    """Blobs that survive the background of their own aligned window."""
    from scipy import ndimage

    background = np.median(aligned, axis=0)
    difference = np.abs(aligned[len(aligned) // 2] - background)
    # How well the alignment worked. Over flat sediment a single shift describes the
    # whole frame and this collapses to sensor noise; over coral rubble, where near
    # and far branches move by different amounts, it does not, and no amount of
    # thresholding afterwards can separate an animal from that.
    if float(np.median(difference)) > RESIDUAL_LIMIT:
        return None
    noise = np.median(np.abs(aligned - background), axis=0)
    mask = difference > np.maximum(DIFFERENCE_FLOOR, NOISE_MULTIPLE * noise)
    return _blobs(mask, min_area_fraction, ndimage)


def _blobs(mask: np.ndarray, min_area_fraction: float, ndimage):
    solid = ndimage.binary_closing(mask, structure=np.ones((3, 3)), iterations=2)
    labels, found = ndimage.label(solid)
    if not found:
        return None
    areas = np.bincount(labels.ravel())[1:]
    keep = areas >= max(16, int(min_area_fraction * mask.size))
    if not keep.any():
        return None
    biggest = int(np.argmax(np.where(keep, areas, 0))) + 1
    share = float(areas[biggest - 1] / mask.size)
    if share > MAX_AREA_FRACTION:
        return None
    rows, columns = np.where(labels == biggest)
    return (float(keep.sum()), share,
            (int(columns.min()), int(rows.min()), int(columns.max()), int(rows.max())))
