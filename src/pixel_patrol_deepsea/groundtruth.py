"""Scoring a report against per-frame ground truth.

Every other number this package produces is a claim. On one expedition it is
checkable: MBARI's DeepSea-MOT ships a `gt.txt` beside each recording with every
animal in every frame boxed, in MOT format. So the same detections that fill the
gallery can be matched against what is actually there, and the answer put on the
page next to them.

    frame, id, x, y, w, h, conf, class, visibility

Only the frame and the box are used. The benchmark tracks identities and this does
not; the question here is the simpler one - when the detector says an animal is at
this spot at this moment, is one there.

Matching is done in **time**, not in frame numbers, and that is the whole trick.
A coarse first pass may analyse a re-encoded copy at ten frames a second, which
renumbers every frame - frame 40 of the copy is frame 120 of the original. Seconds
survive that untouched, so a detection at 4.0 s is compared against whatever the
ground truth says was at 4.0 s, whatever either side counted frames in. Analysing
coarsely costs recall, because frames nobody looked at hold animals nobody found;
it does not licence scoring the wrong frame.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

MATCH_IOU = 0.3       # overlap at which a detection is counted as finding a box


# The precisions worth quoting an answer at. "As many animals as possible without
# false positives" is not one number, it is this curve: the report keeps every
# detection down to a low floor and the viewer has a threshold a reader can move,
# so the useful statement is how much can be found while staying this clean.
FLOORS = (0.99, 0.95, 0.90)


@dataclass
class Score:
    """How one recording did against its own ground truth.

    Both a single verdict - the precision and recall of everything in the report -
    and the curve behind it, because the single verdict is taken at whatever floor
    the pipeline happened to write and that is not the operating point anybody
    would choose. `at_precision` is the interesting reading.
    """
    recording: str
    frames_judged: int = 0
    detections: int = 0
    truth_boxes: int = 0
    matched: int = 0
    # Every annotated box in the recording, including on the frames nobody looked
    # at. See `recall_overall` for why both numbers are kept.
    truth_boxes_all: int = 0
    frames_annotated: int = 0
    # Distinct animals, by the identity the benchmark tracks them under, and the
    # ones a detection landed on at least once anywhere in the recording.
    animals_all: Set[int] = field(default_factory=set)
    animals_found: Set[int] = field(default_factory=set)
    # (confidence, precision, recall) at every confidence, most confident first.
    curve: List[Tuple[float, float, float]] = field(default_factory=list)
    # (confidence, landed on an animal) per detection. Kept because curves cannot
    # be combined - averaging two recordings' precision is not the precision of
    # the two together - so pooling has to go back to the detections.
    judged: List[Tuple[float, bool]] = field(default_factory=list)

    @property
    def precision(self) -> Optional[float]:
        return self.matched / self.detections if self.detections else None

    @property
    def recall(self) -> Optional[float]:
        """Of the animals on the frames the detector looked at, how many it found.

        This isolates the model from the sampling policy, which is the right
        question when the model is what changed.
        """
        return self.matched / self.truth_boxes if self.truth_boxes else None

    @property
    def recall_overall(self) -> Optional[float]:
        """Of every animal in the recording, how many the report contains.

        The honest headline, and the one a reader of the report actually gets. A
        frame nobody looked at holds animals nobody found, and calling those
        out-of-scope measures the detector rather than the pipeline. The gap
        between this and `recall` is exactly what sampling costs.
        """
        return self.matched / self.truth_boxes_all if self.truth_boxes_all else None

    @property
    def recall_animals(self) -> Optional[float]:
        """Of the distinct animals in the recording, how many were found at all.

        The number that answers whether frames are being skipped too hard, and the
        only one of the three that counts creatures rather than boxes. This
        benchmark annotates the same animal in every frame it is visible - 94
        animals in BD become 28,708 boxes - so a box-level recall over the whole
        recording mostly measures how often each animal was re-found, and one
        sighting is all it takes to put a creature in the report.
        """
        return (len(self.animals_found) / len(self.animals_all)
                if self.animals_all else None)

    @property
    def looked_at(self) -> Optional[float]:
        """The fraction of annotated frames the detector actually read."""
        return (self.frames_judged / self.frames_annotated
                if self.frames_annotated else None)

    def overall_at_precision(self, floor: float) -> Optional[float]:
        """Recall over the whole recording at a precision floor.

        Derived rather than recomputed: the same detections matched the same
        animals, so only what they are divided by changes.
        """
        reachable, _confidence = self.at_precision(floor)
        if reachable is None or not self.truth_boxes_all:
            return None
        return reachable * self.truth_boxes / self.truth_boxes_all

    def at_precision(self, floor: float) -> Tuple[Optional[float], Optional[float]]:
        """The most recall reachable while precision stays at or above `floor`.

        Read down the curve rather than up: several confidences can clear the
        floor and the one that matters is the lowest, because that is the one that
        keeps the most animals.
        """
        best: Tuple[Optional[float], Optional[float]] = (None, None)
        for confidence, precision, recall in self.curve:
            if precision >= floor and (best[0] is None or recall > best[0]):
                best = (recall, confidence)
        return best

    def __str__(self) -> str:
        def show(value):
            return f"{value:.2f}" if value is not None else "  - "
        return (f"{self.recording[:20]:22} "
                f"{self.frames_judged:4d}/{self.frames_annotated:<4d} frames read  "
                f"{self.detections:5d} found  "
                f"{self.truth_boxes:5d} of {self.truth_boxes_all:<6d} animals in reach  "
                f"precision {show(self.precision)}  "
                f"recall {show(self.recall)} of boxes it looked at, "
                f"{show(self.recall_overall)} of all boxes, "
                f"{show(self.recall_animals)} of the "
                f"{len(self.animals_all):3d} distinct animals")


def read_mot(text: str) -> Dict[int, List[Tuple[float, float, float, float, int]]]:
    """Boxes per frame, as x1 y1 x2 y2 and the identity they belong to.

    The identity is the second column and it is the whole reason this is a
    *tracking* benchmark. Without it, an animal in view for 300 frames is 300
    animals, and every count made from this file is 300 times too big.
    """
    boxes: Dict[int, List[Tuple[float, float, float, float, int]]] = {}
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 6:
            continue
        try:
            frame = int(float(parts[0]))
            track = int(float(parts[1]))
            x, y, w, h = (float(parts[i]) for i in range(2, 6))
        except ValueError:
            continue
        boxes.setdefault(frame, []).append((x, y, x + w, y + h, track))
    return boxes


def score_recording(recording: str, detections: Iterable[dict],
                    truth: Dict[int, List[Tuple[float, float, float, float]]],
                    fps: float, first_frame: int = 1,
                    match_iou: float = MATCH_IOU) -> Score:
    """Match one recording's detections against its ground truth, by time.

    Judged only on the moments the detector actually looked at. Scoring it on frames
    it never saw would report a recall near zero and mean nothing: how often to look
    is a separate decision from whether the model can see an animal when it does.
    """
    result = Score(recording=recording,
                   frames_annotated=len(truth),
                   truth_boxes_all=sum(len(boxes) for boxes in truth.values()),
                   animals_all={box[4] for boxes in truth.values() for box in boxes
                                if len(box) > 4})
    if fps <= 0:
        return result
    truth_at = {frame: boxes for frame, boxes in truth.items()}
    half = 0.5 / fps
    seen: Dict[int, List[dict]] = {}
    for detection in detections:
        second = detection.get("second")
        if second is None:
            continue
        frame = round(float(second) * fps) + first_frame
        # Only judge against a frame the truth actually annotates, and only when the
        # detection lands within half a frame of it.
        nearest = min((f for f in (frame - 1, frame, frame + 1) if f in truth_at),
                      key=lambda f: abs((f - first_frame) / fps - float(second)),
                      default=None)
        if nearest is None or abs((nearest - first_frame) / fps - float(second)) > half * 3:
            continue
        seen.setdefault(nearest, []).append(detection)

    judged: List[Tuple[float, bool]] = []
    for frame, found in sorted(seen.items()):
        wanted = truth_at.get(frame, [])
        result.frames_judged += 1
        result.detections += len(found)
        result.truth_boxes += len(wanted)
        hits = _judge(found, wanted, match_iou)
        result.matched += sum(bool(landed_on) for _conf, landed_on in hits)
        result.animals_found.update(landed_on for _conf, landed_on in hits
                                    if landed_on is not None)
        judged += [(conf, landed_on is not None) for conf, landed_on in hits]
    result.judged = judged
    result.curve = _curve(judged, result.truth_boxes)
    return result


def pool(scores: Sequence[Score], recording: str = "all together") -> Score:
    """Every scored recording as one curve, which is what a single floor means.

    The per-recording numbers answer "how did it do on this footage". A reader
    moving one threshold in the viewer is asking the other question - what does
    this setting cost me - and the threshold does not know which recording a row
    came from, so that answer is the pooled curve.
    """
    together = Score(
        recording=recording,
        frames_judged=sum(s.frames_judged for s in scores),
        frames_annotated=sum(s.frames_annotated for s in scores),
        detections=sum(s.detections for s in scores),
        truth_boxes=sum(s.truth_boxes for s in scores),
        truth_boxes_all=sum(s.truth_boxes_all for s in scores),
        # Identities are per recording, so pooling them needs the recording in the
        # key or BD's animal 7 and BS's animal 7 become one animal.
        animals_all={(s.recording, a) for s in scores for a in s.animals_all},
        animals_found={(s.recording, a) for s in scores for a in s.animals_found},
        matched=sum(s.matched for s in scores),
        judged=[row for s in scores for row in s.judged])
    together.curve = _curve(together.judged, together.truth_boxes)
    return together


def _curve(judged: List[Tuple[float, bool]], truth_total: int):
    """Precision and recall as the confidence floor is lowered.

    Every detection in the report in one ranking, most confident first, so each
    point is what the report would say if the floor were set there. Matching is
    already done: whether a box lands on an animal does not depend on where the
    floor is.
    """
    hits, points = 0, []
    for rank, (confidence, hit) in enumerate(sorted(judged, key=lambda j: -j[0]), 1):
        hits += hit
        points.append((round(confidence, 5), hits / rank,
                       hits / truth_total if truth_total else 0.0))
    return points


def first_annotated_frame(truth: Dict[int, List[tuple]]) -> int:
    """Whether this file counts frames from zero or from one."""
    return 1 if (truth and min(truth) >= 1) else 0


def _judge(found: Sequence[dict], wanted: Sequence[tuple],
           match_iou: float) -> List[Tuple[float, Optional[int]]]:
    """Per detection, its confidence and which animal it landed on, if any.

    Most confident first, and each truth box can only be claimed once, so a
    second box on the same animal counts against precision rather than for
    recall - which is the whole reason suppression has to work.

    The identity rather than a yes/no, so the same animal found on two different
    frames can be recognised as one creature having been found.
    """
    taken = set()
    out: List[Tuple[float, Optional[int]]] = []
    for detection in sorted(found, key=lambda d: -float(d.get("conf") or 0)):
        box = detection.get("box") or (0, 0, 0, 0)
        best, at = match_iou, None
        for index, truth_box in enumerate(wanted):
            if index in taken:
                continue
            overlap = _iou(box, truth_box)
            if overlap >= best:
                best, at = overlap, index
        landed_on = None
        if at is not None:
            taken.add(at)
            landed_on = wanted[at][4] if len(wanted[at]) > 4 else at
        out.append((float(detection.get("conf") or 0.0), landed_on))
    return out


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    overlap = max(0.0, right - left) * max(0.0, bottom - top)
    union = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap)
    return overlap / union if union > 0 else 0.0
