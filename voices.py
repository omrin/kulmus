"""Speaker voiceprints: refining diarization clusters and recognising known voices by name.

Raw WeSpeaker embeddings are all very similar to each other (cosine 0.75-0.97 between different
people). Subtracting the mean embedding of many voices ("centering") and averaging over all of a
speaker's turns separates them well: on real Hebrew meetings the same person in different parts of a
conversation scores 0.55-0.9, different people mostly below 0.3. Those speaker-level vectors are used to

  * merge clusters the diarizer split too finely (it is run with a permissive threshold),
  * absorb tiny fragments into the closest speaker,
  * recognise people the user has named before (profiles stored locally, never uploaded).
"""

import json
import os
import re
import uuid
from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent / "models"
EMB_MODEL = MODELS_DIR / "wespeaker.onnx"
MEAN_FILE = MODELS_DIR / "wespeaker_mean.npy"
SR = 16_000
MERGE_SIM = 0.5        # speaker-level similarity above which two clusters are the same person
ID_SIM = 0.5           # similarity to a saved voice needed to put its name on a speaker
MIN_TURN = 0.8         # s: shorter turns are too short for a reliable embedding
MAX_PER_SPEAKER = 120  # s of audio embedded per speaker (enough for a stable voiceprint)
TINY = 2.0             # s: a cluster with less speech than this is a fragment ...
TINY_SHARE = 0.02      # ... and so is one with under 2% of all speech (in long recordings)
# Phone calls and other compressed audio make every voice sound alike (two people can reach 0.75), so a
# similarity above MERGE_SIM is only trusted when the clusters cannot be told apart segment by segment:
SURE_SIM = 0.9         # this similar -> the same person, no further test
SEPARABLE = 2.5        # mean d' between the diarizer's original clusters of the two groups that marks two people
MIN_SEP_SEGS = 5       # segments a cluster needs for the separability test

DEFAULT_NAME = re.compile(r"^(דובר|משתתף)( \d+)?$")


class Embedder:
    def __init__(self, threads: int = 4):
        import sherpa_onnx
        self.ext = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL), num_threads=threads))
        self.mean = np.load(MEAN_FILE) if MEAN_FILE.exists() else None

    def embed(self, audio: np.ndarray) -> np.ndarray:
        s = self.ext.create_stream()
        s.accept_waveform(SR, audio)
        s.input_finished()
        v = np.asarray(self.ext.compute(s), dtype=np.float64)
        return v / (np.linalg.norm(v) or 1.0)

    def center(self, v: np.ndarray) -> np.ndarray:
        if self.mean is not None:
            v = v - self.mean
        return v / (np.linalg.norm(v) or 1.0)

    def centroid(self, members) -> np.ndarray:
        """members: [(raw embedding, weight)] -> centered, unit-length speaker vector."""
        return self.center(np.average(np.array([m for m, _ in members]), axis=0, weights=[w for _, w in members]))


def separability(ma, mb, embedder) -> float:
    """How clearly two clusters are different voices: d' of each segment's margin between its own cluster
    (leave-one-out) and the other cluster. Same person split in two: ~0.5-1.5; two people: 2-6."""
    margins = []
    for own, other in ((ma, mb), (mb, ma)):
        oc = embedder.centroid(other)
        xs = np.array([x for x, _ in own])
        ws = np.array([w for _, w in own])
        tot, wsum = (ws[:, None] * xs).sum(axis=0), ws.sum()
        for x, w in own:
            if wsum - w <= 0:
                continue
            cx = embedder.center(x)
            margins.append(float(cx @ embedder.center((tot - w * x) / (wsum - w)) - cx @ oc))
    if len(margins) < 2:
        return 0.0
    m = np.array(margins)
    return float(m.mean() / (m.std() + 1e-9))


def _members(audio, turns, embedder, label):
    out, used = [], 0.0
    spans = sorted(((s, e) for s, e, k in turns if k == label and e - s >= MIN_TURN), key=lambda x: x[0] - x[1])
    for s, e in spans:
        if used >= MAX_PER_SPEAKER:
            break
        out.append((embedder.embed(audio[int(s * SR):int(e * SR)]), e - s))
        used += e - s
    return out


def refine(audio: np.ndarray, turns, embedder: Embedder, fixed: bool = False):
    """Merges over-split clusters and absorbs fragments.

    turns: [(start, end, label)]. Returns (new_turns, {label: (vector, seconds)}). A fragment without a
    usable embedding joins the speaker who talks closest to it in time.
    fixed: the user gave the number of speakers -> keep the diarizer's clusters, only compute vectors."""
    labels = sorted({k for _, _, k in turns})
    secs = {k: sum(e - s for s, e, kk in turns if kk == k) for k in labels}
    mem = {k: _members(audio, turns, embedder, k) for k in labels}
    alias = {k: k for k in labels}

    def root(k):
        while alias[k] != k:
            k = alias[k]
        return k

    def vec(k):
        return embedder.centroid(mem[k]) if mem[k] else None

    if not fixed:
        # The separability of two groups is the average over the diarizer's original clusters in them:
        # testing the merged groups directly dilutes it, because a grown group mixes sub-clusters.
        group = {k: [k] for k in labels}
        orig_secs = dict(secs)
        testable = [k for k in labels if len(mem[k]) >= MIN_SEP_SEGS]
        mem_orig = {k: list(mem[k]) for k in labels}
        dp = {}

        def pair_dprime(i, j):
            key = frozenset((i, j))
            if key not in dp:
                dp[key] = separability(mem_orig[i], mem_orig[j], embedder)
            return dp[key]

        def apart_groups(a, b):
            pairs = [(i, j) for i in group[a] for j in group[b] if i in testable and j in testable]
            if not pairs:
                return False
            w = np.array([orig_secs[i] * orig_secs[j] for i, j in pairs])
            d = np.array([pair_dprime(i, j) for i, j in pairs])
            return float((w * d).sum() / w.sum()) >= SEPARABLE

        apart = set()                    # pairs found to be different people (until one of them changes)
        while True:
            live = [k for k in labels if root(k) == k and mem[k]]
            vs = {k: vec(k) for k in live}
            cands = sorted(((float(vs[a] @ vs[b]), a, b) for i, a in enumerate(live) for b in live[i + 1:]
                            if frozenset((a, b)) not in apart), reverse=True)
            pair = None
            for sim, a, b in cands:
                if sim < MERGE_SIM:
                    break
                if sim < SURE_SIM and apart_groups(a, b):
                    apart.add(frozenset((a, b)))
                    continue
                pair = (a, b)
                break
            if pair is None:
                break
            a, b = pair
            apart = {p for p in apart if a not in p and b not in p}
            keep, gone = (a, b) if secs[a] >= secs[b] else (b, a)
            alias[gone] = keep
            group[keep] += group[gone]
            mem[keep] += mem[gone]
            secs[keep] += secs[gone]
            mem[gone], secs[gone] = [], 0.0
        # fragments: tiny clusters go to the most similar real speaker (or to a neighbour if unusable)
        total = sum(secs[k] for k in labels if root(k) == k) or 1.0
        live = [k for k in labels if root(k) == k]
        big = [k for k in live if secs[k] >= max(TINY, TINY_SHARE * total)]
        if not big and live:
            big = [max(live, key=lambda k: secs[k])]
        def nearest_in_time(k):
            mine = [((s + e) / 2) for s, e, kk in turns if root(kk) == k]
            best_b, best_d = big[0], float("inf")
            for s, e, kk in turns:
                b = root(kk)
                if b in big:
                    d = min(abs((s + e) / 2 - m) for m in mine)
                    if d < best_d:
                        best_b, best_d = b, d
            return best_b

        for k in live:
            if k in big:
                continue
            v = vec(k)
            cands = [b for b in big if mem[b]]
            alias[k] = max(cands, key=lambda b: float(v @ vec(b))) if v is not None and cands else nearest_in_time(k)

    def final(k):
        while k is not None and alias.get(k, k) != k:
            k = alias[k]
        return k

    new_turns = [(s, e, final(k)) for s, e, k in turns]
    vectors = {}
    for k in {k for _, _, k in new_turns if k is not None}:
        v = vec(k)
        if v is not None:
            vectors[k] = (v, secs[k])
    return new_turns, vectors


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
        return {n: p for n, p in data.items() if isinstance(p, dict) and "v" in p}
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
    profiles[name] = {"v": [round(float(x), 5) for x in c], "sec": round(sec, 1)}
    return profiles


def similarity(vector, profile) -> float:
    return float(np.asarray(vector) @ np.asarray(profile["v"]))
