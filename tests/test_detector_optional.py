"""The detector is optional, so what matters most is how it behaves without one."""
import numpy as np

from pixel_patrol_base.core.record import record_from
from pixel_patrol_deepsea import detector, plugin_registry
from pixel_patrol_deepsea.detector_processor import (
    DETECTION_CONFIDENCE,
    DETECTION_TOP_CLASS,
    FathomNetDetectorProcessor,
    _top_class_agg,
)


def test_registry_always_offers_the_free_processors():
    names = {p.NAME for p in plugin_registry.register_processor_plugins()}
    assert {"raster-temporal", "raster-particles", "slice-thumbnail"} <= names


def test_detector_is_offered_only_when_it_can_run():
    offered = any(p.NAME == "raster-detections" for p in plugin_registry.register_processor_plugins())
    assert offered == detector.is_available()


def test_missing_weights_read_as_unavailable(monkeypatch):
    monkeypatch.setattr(detector, "weights_path", lambda: None)
    assert detector.is_available() is False


def test_a_slice_too_small_to_detect_in_yields_no_row():
    proc = FathomNetDetectorProcessor()
    tiny = record_from(np.zeros((2, 8, 8, 3), np.uint8), {"dim_order": "TYXC"})
    assert proc.run_chunk(tiny) == {}


def test_only_the_first_colour_channel_is_looked_at():
    # The pipeline hands each channel its own leaf block, and running the model
    # three times on three greyscale copies of one moment costs triple for an
    # answer the aggregation then takes the maximum of.
    proc = FathomNetDetectorProcessor()
    channel = lambda c: record_from(np.zeros((2, 8, 8, 3), np.uint8),
                                    {"dim_order": "TYXC", "fps": 30.0, "dim_t": 0,
                                     "dim_c": c})
    assert proc._skip(channel(0)) is False
    assert proc._skip(channel(1)) is True
    assert proc._skip(channel(2)) is True


def test_a_long_slice_is_read_at_several_moments(monkeypatch):
    # How often to look and how long a slice is used to be the same decision,
    # because the processor read one frame per slice: asking to look every second
    # meant one-second slices, which meant five times as many cached stills and
    # clips for a finer timeline nobody asked for.
    import pixel_patrol_deepsea.detector_processor as module
    monkeypatch.setattr(module, "DETECT_EVERY_SECONDS", 1.0)
    # five seconds of ten-frames-a-second footage, one look a second
    assert module._moments_to_read(50, 10.0) == [5, 15, 25, 35, 45]
    # a one-second slice already is one look
    assert len(module._moments_to_read(10, 10.0)) == 1
    # and a slice shorter than the interval is one look too, not none
    assert len(module._moments_to_read(10, 30.0)) == 1


def test_looking_more_often_reads_more_of_the_slice(monkeypatch):
    import pixel_patrol_deepsea.detector_processor as module
    monkeypatch.setattr(module, "DETECT_EVERY_SECONDS", 0.5)
    assert len(module._moments_to_read(50, 10.0)) == 10
    monkeypatch.setattr(module, "DETECT_EVERY_SECONDS", 5.0)
    assert len(module._moments_to_read(50, 10.0)) == 1


def test_a_slice_with_no_frame_rate_is_still_read_once(monkeypatch):
    import pixel_patrol_deepsea.detector_processor as module
    monkeypatch.setattr(module, "DETECT_EVERY_SECONDS", 1.0)
    assert len(module._moments_to_read(50, None)) == 1
    assert module._moments_to_read(0, 10.0) == []


def test_the_named_taxon_comes_from_the_most_confident_row():
    spec = next(m for m in FathomNetDetectorProcessor.METRICS if m.name == DETECTION_TOP_CLASS)
    rows = [
        {DETECTION_TOP_CLASS: "shrimp", DETECTION_CONFIDENCE: 0.3},
        {DETECTION_TOP_CLASS: "scyphozoa", DETECTION_CONFIDENCE: 0.9},
        {DETECTION_TOP_CLASS: "beroe", DETECTION_CONFIDENCE: 0.5},
    ]
    assert _top_class_agg(spec, rows) == "scyphozoa"


def test_no_taxon_when_nothing_was_seen():
    spec = next(m for m in FathomNetDetectorProcessor.METRICS if m.name == DETECTION_TOP_CLASS)
    assert _top_class_agg(spec, [{DETECTION_CONFIDENCE: 0.0}]) is None


def test_only_the_first_colour_channel_is_looked_at():
    """Leaf blocks are usually per-channel. Running the model on all three costs
    triple for three greyscale copies of the same moment."""
    proc = FathomNetDetectorProcessor()
    at = lambda channel: record_from(
        np.zeros((2, 8, 8, 3), np.uint8),
        {"dim_order": "TYXC", "fps": 30.0, "dim_t": 0, "dim_c": channel})
    assert proc._skip(at(0)) is False
    assert proc._skip(at(1)) is True
    assert proc._skip(at(2)) is True


def test_footage_without_a_channel_axis_is_still_looked_at():
    proc = FathomNetDetectorProcessor()
    grey = record_from(np.zeros((2, 8, 8), np.uint8),
                       {"dim_order": "TYX", "fps": 30.0, "dim_t": 0})
    assert proc._skip(grey) is False


def test_analysing_without_a_detector_has_to_be_asked_for(monkeypatch, tmp_path):
    """The failure this prevents was silent and expensive.

    A processor that cannot run is not registered, and `--processors-include` for
    an unregistered name asks for nothing and gets it - so a worker without the
    detector writes a perfectly good report with no animals in it and exits zero.
    A whole cluster run came back that way: positions, colour, motion, and not one
    detection, because the compute nodes never saw XDG_CACHE_HOME.
    """
    import pytest

    from pixel_patrol_deepsea import collect, detector

    monkeypatch.setattr(detector, "is_available", lambda: False)
    with pytest.raises(SystemExit, match="no detector"):
        collect.analyse_one("https://example/x.mp4", tmp_path / "x.parquet", "DSMOT",
                            fps=10, slice_frames=10, detector="general",
                            detector_frames=1)
    # ...and saying so explicitly is allowed, because it is a real thing to want
    collect._insist_on_the_detector("none")


def test_the_message_names_the_cache_it_looked_in(monkeypatch):
    import pytest

    from pixel_patrol_deepsea import collect, detector

    monkeypatch.setattr(detector, "is_available", lambda: False)
    with pytest.raises(SystemExit) as refused:
        collect._insist_on_the_detector("general")
    said = str(refused.value)
    assert str(detector.CACHE) in said
    assert "XDG_CACHE_HOME" in said
