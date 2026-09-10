"""Temporal metric kernels (T moved to the first axis).

Every other raster kernel in PixelPatrol reduces *within* one frame, so none of
them can see movement. These reduce *between* consecutive frames along T, which
is what separates a moving camera from a parked one and a live recording from a
frozen one.
"""

from typing import Optional

import numpy as np


def frames_first(arr: np.ndarray, dim_order: str) -> np.ndarray:
    """View of arr with the T axis moved to the front, so frames can be walked in order."""
    return np.moveaxis(arr, dim_order.index("T"), 0)


def consecutive_frame_differences(frames: np.ndarray) -> np.ndarray:
    """Mean absolute difference between each consecutive pair of frames.

    Returns one value per pair, so a block of n frames yields n-1 values and a
    single frame yields none.

    Pairs are walked one at a time rather than differencing the whole block at
    once: `np.diff` on a 30-frame block of 4K RGB would allocate ~750 MB as
    float32, while one pair needs two frames.
    """
    if frames.shape[0] < 2:
        return np.empty(0, dtype=np.float64)
    differences = np.empty(frames.shape[0] - 1, dtype=np.float64)
    previous = _as_float32(frames[0])
    for i, frame in enumerate(frames[1:]):
        current = _as_float32(frame)
        differences[i] = np.nanmean(np.abs(current - previous))
        previous = current
    return differences


def _as_float32(frame: np.ndarray) -> np.ndarray:
    """Promote one frame so unsigned subtraction cannot wrap around."""
    return frame if frame.dtype == np.float32 else frame.astype(np.float32)


def mean_frame_difference(differences: np.ndarray) -> Optional[float]:
    """Average movement across the block; 0.0 for a frozen or duplicated stretch."""
    return float(np.nanmean(differences)) if differences.size else None


def max_frame_difference(differences: np.ndarray) -> Optional[float]:
    """Largest single-pair jump in the block, which a mean over 30 frames would dilute.

    This is what survives a hard cut, a lighting switch, or a subject entering
    the frame in one or two frames.
    """
    return float(np.nanmax(differences)) if differences.size else None
