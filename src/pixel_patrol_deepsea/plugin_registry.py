from pathlib import Path

from pixel_patrol_deepsea import detector
from pixel_patrol_deepsea.location_processor import SliceLocationProcessor
from pixel_patrol_deepsea.motion_processor import MovingObjectProcessor
from pixel_patrol_deepsea.particle_processor import BrightParticleProcessor
from pixel_patrol_deepsea.slice_colour_processor import SliceColourProcessor
from pixel_patrol_deepsea.slice_thumbnail_processor import SliceThumbnailProcessor
from pixel_patrol_deepsea.temporal_processor import TemporalMetricsProcessor


def register_processor_plugins():
    """The detector is offered only when its weights and code are actually present.

    It carries a model and, unavoidably, GPL-licensed loading code, so it is not a
    dependency of this package - see `detector` for how to set it up. Registering it
    unconditionally would put a processor in the list that fails for most people.
    """
    processors = [TemporalMetricsProcessor, BrightParticleProcessor,
                  MovingObjectProcessor, SliceThumbnailProcessor,
                  SliceColourProcessor, SliceLocationProcessor]
    if detector.is_available():
        from pixel_patrol_deepsea.detector_processor import FathomNetDetectorProcessor
        processors.append(FathomNetDetectorProcessor)
    return processors


def register_loader_plugins():
    """The expedition manifest reader. Always available: it is a JSON file and a
    delegation to the video loader, with no model or optional dependency behind it."""
    from pixel_patrol_deepsea.expedition_loader import ExpeditionLoader

    return [ExpeditionLoader]


def get_viewer_extension_dir():
    return Path(__file__).parent / "viewer"
