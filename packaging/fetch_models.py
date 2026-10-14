"""Downloads the small speaker-separation models into models/ (the 1.6 GB transcription model is
downloaded by the app itself on first run). models/wespeaker_mean.npy is part of the repository."""

import tarfile
import urllib.request
from pathlib import Path

MODELS = Path(__file__).resolve().parent.parent / "models"
BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEG = "sherpa-onnx-pyannote-segmentation-3-0"


def fetch(url, dest: Path):
    print("downloading", url)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)


def main():
    MODELS.mkdir(exist_ok=True)
    if not (MODELS / SEG / "model.onnx").exists():
        arc = MODELS / f"{SEG}.tar.bz2"
        fetch(f"{BASE}/speaker-segmentation-models/{SEG}.tar.bz2", arc)
        with tarfile.open(arc) as t:
            t.extractall(MODELS)
        arc.unlink()
    if not (MODELS / "wespeaker.onnx").exists():
        fetch(f"{BASE}/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx", MODELS / "wespeaker.onnx")
    print("models ready:", sorted(p.name for p in MODELS.iterdir()))


if __name__ == "__main__":
    main()
