"""Speaker voiceprints and recognising known voices by name.

Voiceprints are WeSpeaker ResNet34 embeddings computed the way pyannote computes them (diarize.Voiceprints): the
mean of the input features is removed, so the recording channel (phone, microphone) does not dominate the voice.
Measured on Hebrew recordings labelled by transcribe.ivrit.ai: the same speaker in two halves of a recording
scores a median of 0.93, different speakers of one recording at most 0.43, one person recurring across
recordings 0.85-0.9. Names the user gives are kept as profiles (stored locally, never uploaded) and recognised
in later recordings.
"""

import json
import os
import re
import uuid
from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent / "models"
EMB_MODEL = MODELS_DIR / "wespeaker.onnx"
SR = 16_000
VOICE_MODEL = "wespeaker-r34-cmn"  # kind of voiceprint; older kinds (before 1.0.3) cannot be compared to it
ID_SIM = 0.6           # similarity to a saved voice needed to put its name on a speaker
MIN_TURN = 0.8         # s: shorter turns are too short for a reliable voiceprint
MAX_PER_SPEAKER = 120  # s of audio embedded per speaker (enough for a stable voiceprint)

DEFAULT_NAME = re.compile(r"^(דובר|משתתף)( \d+)?$")


class Embedder:
    def __init__(self, threads: int = 4, voiceprints=None):
        if voiceprints is None:
            import diarize
            voiceprints = diarize.Voiceprints(EMB_MODEL, threads)
        self.vp = voiceprints

    def embed(self, audio: np.ndarray) -> np.ndarray:
        return self.center(self.vp.embed(audio))

    def center(self, v: np.ndarray) -> np.ndarray:
        return v / (np.linalg.norm(v) or 1.0)

    def centroid(self, members) -> np.ndarray:
        """members: [(embedding, weight)] -> unit-length speaker vector."""
        return self.center(np.average(np.array([m for m, _ in members]), axis=0, weights=[w for _, w in members]))


def _members(audio, turns, embedder, label):
    out, used = [], 0.0
    spans = sorted(((s, e) for s, e, k in turns if k == label and e - s >= MIN_TURN), key=lambda x: x[0] - x[1])
    for s, e in spans:
        if used >= MAX_PER_SPEAKER:
            break
        out.append((embedder.embed(audio[int(s * SR):int(e * SR)]), e - s))
        used += e - s
    return out


def speaker_vectors(audio: np.ndarray, turns, embedder: Embedder) -> dict:
    """{label: (voiceprint, seconds of speech)} for the speakers of diarization turns [(start, end, label)]."""
    vectors = {}
    for k in sorted({k for _, _, k in turns}):
        m = _members(audio, turns, embedder, k)
        if m:
            vectors[k] = (embedder.centroid(m), sum(e - s for s, e, kk in turns if kk == k))
    return vectors


def is_current(voiceprint) -> bool:
    """A stored voiceprint ({"v", "sec", "m"}) of the kind this version computes."""
    return isinstance(voiceprint, dict) and "v" in voiceprint and voiceprint.get("m") == VOICE_MODEL


def voice_of_spans(audio, spans, embedder):
    """Vector for one known speaker from (start, end) spans (e.g. the local user in a call)."""
    m = _members(audio, [(s, e, 0) for s, e in spans], embedder, 0)
    if not m:
        return None
    return embedder.centroid(m), sum(e - s for s, e in spans)


def identify(vectors: dict, profiles: dict) -> dict:
    """{label: name} for speakers matching a saved voice; each name and speaker used at most once."""
    if not vectors or not profiles:
        return {}
    pairs = []
    for k, (v, _) in vectors.items():
        for name, p in profiles.items():
            sim = float(v @ np.asarray(p["v"]))
            if sim >= ID_SIM:
                pairs.append((sim, k, name))
    out, used = {}, set()
    for sim, k, name in sorted(pairs, reverse=True):
        if k not in out and name not in used:
            out[k] = name
            used.add(name)
    return out


# --------------------------------------------------------------------------- saved voices

def profiles_path() -> Path:
    import library
    return library.app_dir() / "voices.json"


def load_profiles(path: Path | None = None) -> dict:
    path = path or profiles_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {n: p for n, p in data.items() if is_current(p)}       # older voices cannot be compared
    except (OSError, ValueError):
        return {}


def save_profiles(profiles: dict, path: Path | None = None):
    path = path or profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(profiles, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def learn(profiles: dict, name: str, vector, seconds: float, cap: float = 600.0) -> dict:
    """Adds speech of `name` to its saved voice (a duration-weighted running average)."""
    v = np.asarray(vector, dtype=np.float64)
    old = profiles.get(name)
    if old:
        w_old = min(float(old.get("sec", 0)), cap)
        c = np.asarray(old["v"]) * w_old + v * seconds
        sec = w_old + seconds
    else:
        c, sec = v, seconds
    c = c / (np.linalg.norm(c) or 1.0)
    profiles[name] = {"v": [round(float(x), 5) for x in c], "sec": round(sec, 1), "m": VOICE_MODEL}
    return profiles


def similarity(vector, profile) -> float:
    return float(np.asarray(vector) @ np.asarray(profile["v"]))
