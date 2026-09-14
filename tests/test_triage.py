"""What each slice of footage was doing.

These are the rules the viewer used to own, ported so there is one implementation
instead of two. Checked against the JavaScript over a real expedition report -
36 recordings, every window boundary and duration identical - and kept here so
the two cannot drift apart now that only one of them is in use.
"""

import pytest

from pixel_patrol_deepsea.triage import (
    FROZEN_BELOW, Slice, classify, find_windows, percentile, summarise,
    verdict_per_slice,
)


def _line(entries, step=10):
    """A timeline from (movement, structure, detections, movers, top_class) tuples."""
    return [Slice(t=i * step, movement=m, structure=s, detections=d, movers=v, top_class=c)
            for i, (m, s, d, v, c) in enumerate(entries)]


def test_dead_footage_is_dead_whatever_else_is_true_of_it():
    # Frozen is decided first on purpose: a detection on a duplicated frame is a
    # detection on footage nobody should be watching.
    assert classify(0.0, 100, 5, 1.0, 9.0, 100, 3) == "frozen"


def test_a_named_animal_beats_movement():
    # Movement only ever says the camera moved; the animal is what someone came for.
    assert classify(0.5, 100, 1, 1.0, 9.0, 100, 0) == "subject"


def test_something_moving_with_no_name_is_still_worth_finding():
    """The only signal here that can point at a species no model has a class for."""
    assert classify(0.5, 100, 0, 1.0, 9.0, 100, 2) == "unnamed"


def test_holding_still_on_nothing_is_not_holding_still_on_something():
    detail = 100.0
    assert classify(0.5, 5, 0, 1.0, 9.0, detail, 0) == "empty"     # no detail in frame
    assert classify(0.5, 90, 0, 1.0, 9.0, detail, 0) == "dwell"    # something there


def test_a_slice_that_is_none_of_those_is_left_unjudged():
    assert classify(5.0, 100, 0, 1.0, 9.0, 100, 0) is None


def test_the_thresholds_come_from_the_recording_itself():
    """The same amount of movement means different things in different recordings.

    "Holding still" on a transect and on a descent are not the same number, so the
    lines are percentiles of this recording's own movement - which is why this
    cannot be decided one slice at a time, and why it lives beside `collect one`
    rather than in a processor.
    """
    # 0.5 among much larger movement is this recording holding still...
    calm = _line([(0.5, 100, 0, 0, None)] + [(5.0, 100, 0, 0, None)] * 20)
    # ...and the same 0.5 among much smaller movement is it moving hard.
    busy = _line([(0.5, 100, 0, 0, None)] + [(0.05, 100, 0, 0, None)] * 20)
    assert verdict_per_slice(calm)[0] == "dwell"
    assert verdict_per_slice(busy)[0] == "active"


def test_frozen_slices_are_kept_out_of_the_dwell_threshold():
    # Otherwise a recording that is half dead has a dwell line of zero and nothing
    # else is ever judged to be holding still.
    line = _line([(0.0, 100, 0, 0, None)] * 10 + [(0.5, 100, 0, 0, None)] * 10)
    assert verdict_per_slice(line)[-1] == "dwell"


def test_runs_of_one_kind_become_one_window():
    line = _line([(0.5, 100, 1, 0, "fish")] * 6)
    [window] = find_windows(line)
    assert (window.kind, window.label, window.slices) == ("subject", "fish", 6)


def test_two_species_one_after_the_other_are_two_finds():
    line = _line([(0.5, 100, 1, 0, "fish")] * 3 + [(0.5, 100, 1, 0, "shrimp")] * 3)
    assert [w.label for w in find_windows(line)] == ["fish", "shrimp"]


def test_a_gap_of_a_few_slices_does_not_end_a_run():
    line = _line([(0.5, 100, 1, 0, "fish")] * 3 + [(5.0, 100, 0, 0, None)] * 2
                 + [(0.5, 100, 1, 0, "fish")] * 3)
    fish = [w for w in find_windows(line) if w.label == "fish"]
    assert len(fish) == 1 and fish[0].slices == 8


def test_a_single_slice_of_movement_is_noise():
    line = _line([(5.0, 100, 0, 0, None)] * 20 + [(0.001, 100, 0, 0, None)]
                 + [(5.0, 100, 0, 0, None)] * 20)
    assert not [w for w in find_windows(line) if w.kind == "frozen"]


def test_a_single_slice_with_an_animal_in_it_is_not():
    """A model looked at the frame and named what was in it. Three of the seven
    species in one midwater report are only ever on screen for a single slice, and
    holding them to the same minimum dropped them from the gallery entirely."""
    line = _line([(5.0, 100, 0, 0, None)] * 10 + [(5.0, 100, 1, 0, "beroe")]
                 + [(5.0, 100, 0, 0, None)] * 10)
    assert [w.label for w in find_windows(line) if w.kind == "subject"] == ["beroe"]


def test_seconds_rather_than_slices_because_reports_hold_both():
    line = _line([(0.0, 100, 0, 0, None)] * 10, step=10)
    verdicts = summarise(line, fps=10)
    # Ten slices, ten frames apart, ten frames a second: a second of footage each,
    # and every one of them frozen.
    assert verdicts.seconds["frozen"] == pytest.approx(10.0)
    assert verdicts.total_seconds == pytest.approx(10.0)


def test_without_a_frame_rate_the_numbers_are_frames():
    line = _line([(0.0, 100, 0, 0, None)] * 5, step=10)
    assert summarise(line, fps=None).seconds["frozen"] == pytest.approx(50.0)


def test_the_percentile_is_the_viewer_s_to_the_letter():
    # Nearest-rank with a floor. Not numpy's default, and not worth improving: it
    # decides a threshold compared against the values it came from, and changing it
    # would move every verdict in every report ever written.
    assert percentile([1, 2, 3, 4], 50) == 3
    assert percentile([], 50) == 0
    assert percentile([5], 90) == 5


def test_a_timeline_too_short_to_have_a_shape_has_no_windows():
    assert find_windows(_line([(0.5, 100, 1, 0, "fish")])) == []


def test_codec_noise_is_frozen_not_a_still_camera():
    """Lossy video never differs by exactly zero.

    On a real dive tape the dead tail sat between 0.0001 and 0.0045 while the
    quietest live footage sat at 0.133, so matching exact zeros missed the whole
    dead stretch. The threshold is where that gap is, not at zero.
    """
    dead = _line([(5.0, 100, 0, 0, None)] * 10 + [(0.0004, 100, 0, 0, None)] * 8
                 + [(5.0, 100, 0, 0, None)] * 10)
    assert [w.kind for w in find_windows(dead) if w.kind == "frozen"] == ["frozen"]

    alive = _line([(5.0, 100, 0, 0, None)] * 10 + [(0.133, 100, 0, 0, None)] * 8
                  + [(5.0, 100, 0, 0, None)] * 10)
    assert not [w for w in find_windows(alive) if w.kind == "frozen"]


def test_no_kind_totals_more_than_the_recording_is_long():
    line = _line([(0.0, 100, 0, 0, None)] * 40)
    verdicts = summarise(line, fps=30)
    assert verdicts.seconds["frozen"] <= verdicts.total_seconds


def test_the_verdicts_together_never_total_more_than_the_footage():
    """What the plots read, and what they showed when this was wrong.

    The seconds used to be summed over windows. Windows are merged across gaps of
    up to ten slices and one species' run interleaves with another's, so two
    windows can cover the same slice and the gaps between them belong to whatever
    was in them. The shares that came out were over 100% - 143% of one GOA2004
    recording - which reads as a broken widget, and is.
    """
    # Two species alternating every other slice, the way a busy benthic transect
    # looks, with a dead stretch and a held shot in the middle of it.
    animals = [(5.0, 100, 1, 0, "sea pen" if i % 2 else "urchin") for i in range(20)]
    line = _line(animals[:8] + [(0.0, 100, 0, 0, None)] * 4
                 + [(0.2, 100, 0, 0, None)] * 4 + animals[8:])
    verdicts = summarise(line, fps=30)
    assert sum(verdicts.seconds.values()) <= verdicts.total_seconds + 1e-9
    # ...and the windows still merge across those gaps, which is what they are for.
    assert len([w for w in verdicts.windows if w.kind == "subject"]) < 20


def test_without_a_detail_measurement_a_still_camera_is_a_dwell():
    """`empty` needs something to compare against. With no laplacian_variance and no
    std_intensity in the report there is no baseline, and calling every held shot
    empty would be worse than calling none of them that."""
    blind = _line([(0.5, None, 0, 0, None)] + [(5.0, None, 0, 0, None)] * 20)
    assert verdict_per_slice(blind)[0] == "dwell"


# ── writing the verdicts into a report ────────────────────────────────────────

def _report(path, recordings=2, slices=20):
    """A report shaped like one `collect one` writes: slices, and one aggregate
    row per recording with no slice index on it."""
    import polars as pl
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = {"name": [], "dim_t": [], "frame_difference": [], "std_intensity": [],
            "detection_count": [], "moving_object_count": [], "detection_top_class": [],
            "fps": [], "obs_level": [], "slice_thumbnail": []}
    for r in range(recordings):
        for i in range(slices):
            rows["name"].append(f"dive{r}.mp4")
            rows["dim_t"].append(i * 30)
            rows["frame_difference"].append(0.0 if i < 6 else 5.0)
            rows["std_intensity"].append(100.0)
            rows["detection_count"].append(1.0 if i > 14 else 0.0)
            rows["moving_object_count"].append(0.0)
            rows["detection_top_class"].append("beroe" if i > 14 else None)
            rows["fps"].append(30.0)
            rows["obs_level"].append(1)
            rows["slice_thumbnail"].append(b"\xff\xd8" + bytes([i % 251]) * 2000)
        rows["name"].append(f"dive{r}.mp4")
        rows["dim_t"].append(None)
        for column, value in (("frame_difference", 2.0), ("std_intensity", 100.0),
                              ("detection_count", 0.2), ("moving_object_count", 0.0),
                              ("detection_top_class", None), ("fps", 30.0),
                              ("obs_level", 0)):
            rows[column].append(value)
        rows["slice_thumbnail"].append(None)
    table = pl.DataFrame(rows).to_arrow()
    table = table.replace_schema_metadata({b"pp_project_name": b"Windows to the Deep"})
    pq.write_table(table, path)
    return path


def test_a_judged_report_says_what_every_slice_was_doing(tmp_path):
    from pixel_patrol_deepsea.triage import describe
    import polars as pl

    report = _report(tmp_path / "EX2107.parquet")
    assert describe(report) == 2
    out = pl.read_parquet(report)
    assert out.height == 42
    judged = out.filter(pl.col("dim_t").is_not_null())
    assert set(judged["slice_verdict"].to_list()) >= {"frozen", "subject"}


def test_the_seconds_go_on_the_recording_s_own_row_and_nowhere_else(tmp_path):
    from pixel_patrol_deepsea.triage import describe
    import polars as pl

    report = _report(tmp_path / "EX2107.parquet")
    describe(report)
    out = pl.read_parquet(report)
    said = out.filter(pl.col("footage_seconds").is_not_null())
    assert said.height == 2 and said["obs_level"].to_list() == [0, 0]
    # Twenty slices thirty frames apart at thirty frames a second is twenty seconds.
    assert said["footage_seconds"].to_list() == [20.0, 20.0]
    kinds = ["frozen", "subject", "unnamed", "dwell", "empty", "active"]
    for row in said.iter_rows(named=True):
        assert sum(row[f"verdict_seconds_{k}"] for k in kinds) <= row["footage_seconds"]


def test_judging_a_report_twice_replaces_the_verdicts_rather_than_repeating_them(tmp_path):
    """The rules change, and every report written under the old ones is re-judged.
    Two columns of one name is a file that reads back as whichever the reader picks."""
    from pixel_patrol_deepsea.triage import describe
    import pyarrow.parquet as pq

    report = _report(tmp_path / "EX2107.parquet")
    describe(report)
    describe(report)
    names = pq.read_schema(report).names
    assert names.count("slice_verdict") == 1
    assert names.count("verdict_seconds_frozen") == 1


def test_judging_keeps_what_the_report_already_carried(tmp_path):
    from pixel_patrol_deepsea.triage import describe
    import polars as pl
    import pyarrow.parquet as pq

    report = _report(tmp_path / "EX2107.parquet")
    before = pl.read_parquet(report)
    describe(report)
    after = pl.read_parquet(report)
    assert after["slice_thumbnail"].to_list() == before["slice_thumbnail"].to_list()
    # The viewer reads the title out of the file's own metadata.
    assert (pq.read_schema(report).metadata or {})[b"pp_project_name"] == b"Windows to the Deep"


def test_a_report_is_never_read_whole_to_judge_it(tmp_path, monkeypatch):
    """GOA2004 is 3.37 GB of pictures and 442,338 rows, and the verdicts are made
    of nine columns of numbers. Reading the pictures to judge the numbers is a
    machine nobody has."""
    import polars
    import pyarrow.parquet

    from pixel_patrol_deepsea import triage

    def refuse(*args, **kwargs):
        raise AssertionError("the whole report was read into memory")

    monkeypatch.setattr(pyarrow.parquet, "read_table", refuse)
    monkeypatch.setattr(polars, "read_parquet", refuse)
    # A batch of sixteen rows and a row group of 20 kB, so that a test's worth of
    # data is cut the way an expedition's is.
    monkeypatch.setattr(triage, "READ_ROWS", 16)
    monkeypatch.setattr(triage, "WRITE_BYTES", 20_000)
    report = _report(tmp_path / "EX2107.parquet", recordings=2, slices=40)
    assert triage.describe(report) == 2
    assert pyarrow.parquet.ParquetFile(report).num_row_groups > 1


def test_a_report_with_nothing_to_judge_is_left_alone(tmp_path):
    from pixel_patrol_deepsea.triage import describe
    import polars as pl
    import pyarrow.parquet as pq

    path = tmp_path / "thin.parquet"
    pl.DataFrame({"name": ["a"], "dim_t": [0]}).write_parquet(path)
    assert describe(path) == 0
    assert pq.read_schema(path).names == ["name", "dim_t"]
