"""Fetch a FathomNet detector and the code that loads it, into the cache.

Run once, then `raster-detections` appears in `pixel-patrol list`:

    python -m pixel_patrol_deepsea.fetch_detector

Two pieces come from two places, with two different licences, and neither is a
dependency of this package:

- the weights, CC-BY-4.0, from FathomNet on Hugging Face;
- YOLOv5 v6.2, **GPL-3.0**, from GitHub, because the checkpoint is a pickled model
  object that cannot be unpickled without those exact modules.

Fetching them is therefore your decision, not something installing this package
does on your behalf.
"""

import argparse
import io
import sys
import tarfile
import urllib.request

from pixel_patrol_deepsea.detector import CACHE

YOLOV5 = "https://codeload.github.com/ultralytics/yolov5/tar.gz/refs/tags/v6.2"
HF = "https://huggingface.co"
MODELS = {
    # Gelatinous zooplankton and friends, 22 classes, no fish. Right for open water,
    # confidently wrong on the seafloor - see the README.
    "midwater": f"{HF}/FathomNet/MBARI-midwater-supercategory-detector/resolve/main/best.pt",
    # Fish, and nothing but fish, which is what most benthic dive footage is full of
    # and what the midwater model has no class for at all. MIT licensed.
    "fish": f"{HF}/FathomNet/megafishdetector/resolve/main/weights/megafishdetector_v0_yolov5m_1280p.pt",
    # Trained on 315k FathomNet images across both midwater and benthic imagery: the
    # generalist to reach for when the footage is not obviously one or the other.
    "general": f"{HF}/FathomNet/MBARI-315k-yolov5/resolve/main/mbari_315k_yolov5.pt",
}


def fetch_yolov5(destination):
    if (destination / "models" / "experimental.py").is_file():
        print(f"yolov5 already at {destination}")
        return
    print(f"fetching YOLOv5 v6.2 (GPL-3.0) -> {destination}")
    with urllib.request.urlopen(YOLOV5, timeout=300) as response:
        archive = tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz")
    archive.extractall(destination.parent, filter="data")
    (destination.parent / "yolov5-6.2").rename(destination)


def fetch_weights(url, destination):
    if destination.is_file():
        print(f"weights already at {destination}")
        return
    print(f"fetching weights (CC-BY-4.0) -> {destination}")
    with urllib.request.urlopen(url, timeout=900) as response, open(destination, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="midwater", choices=sorted(MODELS),
                        help="which FathomNet detector to fetch")
    args = parser.parse_args(argv)

    CACHE.mkdir(parents=True, exist_ok=True)
    fetch_yolov5(CACHE / "yolov5")
    fetch_weights(MODELS[args.model], CACHE / f"{args.model}.pt")
    print(f"\nuse it with PIXEL_PATROL_DETECTOR={args.model}")

    from pixel_patrol_deepsea import detector
    print("detector available:", detector.is_available())
    print("\nYOLOv5 is GPL-3.0. The weights are CC-BY-4.0 and cite FathomNet;")
    print("see https://fathomnet.org for terms and attribution.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
