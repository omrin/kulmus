"""Speaker diarization (diarize.py), voiceprints and recognising named voices."""

import os
from pathlib import Path

import numpy as np
import pytest

import fakes
from conftest import wait_until
from test_round2 import write_wav

MODELS = Path(__file__).resolve().parent.parent / "models"


class FakeEmbedder:
    """embed() returns the voice of whoever speaks at that time (vectors given per time range)."""

    def __init__(self, voices_by_time):
        self.voices_by_time = voices_by_time       # [(start, end, vector)]

    def embed(self, audio):
        t = self._t
        for s, e, v in self.voices_by_time:
            if s <= t < e:
                v = np.asarray(v, float)
                return v / np.linalg.norm(v)
        raise AssertionError(t)

    def center(self, v):
        import voices
        return voices.Embedder.center(self, v)

    def centroid(self, members):
        import voices
        return voices.Embedder.centroid(self, members)


A, B = [1, 0, 0, 0], [0, 1, 0, 0]


def test_speaker_vectors_average_each_speakers_turns(monkeypatch):
    import voices
    emb = FakeEmbedder([(0, 10, A), (10, 20, B), (20, 30, [0.9, 0.1, 0, 0])])

    def members(audio, turns, embedder, label):     # tell the fake embedder which turn it embeds
        out = []
        for s, e, k in turns:
            if k == label and e - s >= voices.MIN_TURN:
                embedder._t = s
                out.append((embedder.embed(None), e - s))
        return out

    monkeypatch.setattr(voices, "_members", members)
    vecs = voices.speaker_vectors(np.zeros(16000 * 30, np.float32), [(0, 10, 0), (10, 20, 1), (20, 30, 0)], emb)
    assert set(vecs) == {0, 1}
    assert vecs[0][1] == 20 and vecs[1][1] == 10
    assert vecs[0][0][0] > 0.95 and abs(np.linalg.norm(vecs[0][0]) - 1) < 1e-9


# ----------------------------------------------------------------------------- diarize.py

def _voices(rng, n_per, dims=32, noise=0.15):
    """Voiceprints of len(n_per) people (random, nearly orthogonal directions) with some noise."""
    centers = rng.standard_normal((len(n_per), dims))
    x, y = [], []
    for k, n in enumerate(n_per):
        x.append(centers[k] + noise * np.linalg.norm(centers[k]) * rng.standard_normal((n, dims)) / np.sqrt(dims))
        y += [k] * n
    return np.vstack(x), np.array(y)


def _same_partition(a, b):
    return len(set(zip(a, b))) == len(set(a)) == len(set(b))


def test_cluster_finds_the_people_and_folds_small_clusters_into_large_ones():
    import diarize
    rng = np.random.default_rng(0)
    x, y = _voices(rng, [80, 60, 40])
    assert _same_partition(diarize.cluster(x), y)
    # a few stray voiceprints (fewer than MIN_CLUSTER_SIZE) do not become a speaker of their own
    stray, _ = _voices(rng, [3])
    labels = diarize.cluster(np.vstack([x, stray]))
    assert len(set(labels)) == 3


def test_cluster_can_be_asked_for_a_number_of_speakers():
    import diarize
    x, y = _voices(np.random.default_rng(1), [50, 50, 50])
    assert len(set(diarize.cluster(x, num_clusters=2))) == 2
    assert _same_partition(diarize.cluster(x, num_clusters=3), y)


def test_long_recordings_are_preclustered_with_the_same_result():
    import diarize
    x, y = _voices(np.random.default_rng(2), [diarize.PRECLUSTER_ABOVE // 2 + 50] * 2, noise=0.05)
    assert len(x) > diarize.PRECLUSTER_ABOVE
    assert _same_partition(diarize.cluster(x), y)


class _FakeSeg:
    window, step, frame_shift, receptive = 160_000, 16_000, 270, 991


def test_assign_turns_window_speakers_into_timed_turns():
    """30 s: A talks for the first 15 s, B for the rest; each 10 s window sees them as local speakers 0/1."""
    import diarize
    seg, frames = _FakeSeg(), 589
    starts = list(range(0, 30 * 16000 - seg.window + 1, seg.step))
    binary = np.zeros((len(starts), frames, 3), np.float32)
    emb = np.full((len(starts), 3, 4), np.nan)
    t_frame = (np.arange(frames) * seg.frame_shift + seg.receptive / 2) / 16000
    for c, s0 in enumerate(starts):
        t = s0 / 16000 + t_frame
        binary[c, t < 15, 0] = 1
        binary[c, t >= 15, 1] = 1
        if binary[c, :, 0].any():
            emb[c, 0] = A
        if binary[c, :, 1].any():
            emb[c, 1] = B
    turns = diarize.assign(binary, emb, seg, 30 * 16000, min_cluster=1)
    assert len({k for *_, k in turns}) == 2
    first = [t for t in turns if t[2] == turns[0][2]]
    assert abs(first[0][0]) < 0.2 and abs(first[-1][1] - 15) < 0.2
    assert abs(max(e for _, e, _ in turns) - 30) < 0.3


@pytest.mark.skipif(not (MODELS / "wespeaker.onnx").exists(), reason="models not downloaded")
def test_voiceprints_match_the_original_network():
    """The pre-pooling tensor + our pooling and linear layer reproduce the full ONNX model."""
    import onnxruntime as ort
    import diarize
    rng = np.random.default_rng(3)
    wav = (0.1 * rng.standard_normal(16000 * 3)).astype(np.float32)
    vp = diarize.Voiceprints(MODELS / "wespeaker.onnx", 2)
    f = diarize.fbank(wav)
    assert f.shape == (298, 80)
    f -= f.mean(axis=0)
    full = ort.InferenceSession(str(MODELS / "wespeaker.onnx"), providers=["CPUExecutionProvider"])
    ref = full.run(None, {full.get_inputs()[0].name: f[None]})[0][0]
    assert np.abs(vp.embed(wav) - ref).max() < 1e-3
    seq = vp.frames(wav)
    assert np.allclose(vp.pool(seq, np.ones(589)), vp.pool(seq), atol=1e-3)   # uniform weights = no weights


def test_old_voiceprints_are_never_compared(tmp_path):
    import voices
    path = tmp_path / "voices.json"
    voices.save_profiles({"ישן": {"v": [1, 0, 0], "sec": 30},
                          "חדש": {"v": [1, 0, 0], "sec": 30, "m": voices.VOICE_MODEL}}, path)
    assert set(voices.load_profiles(path)) == {"חדש"}
    assert not voices.is_current({"v": [1, 0], "sec": 3})
    assert voices.learn({}, "x", [1, 0], 5)["x"]["m"] == voices.VOICE_MODEL


def test_identify_is_one_to_one_and_needs_a_close_voice():
    import voices
    vecs = {"a": (np.array([1.0, 0, 0]), 30), "b": (np.array([0.0, 1, 0]), 30), "c": (np.array([0, 0, 1.0]), 30)}
    profiles = {"דנה": {"v": [0.9, 0.1, 0], "sec": 60}, "יוסי": {"v": [0.1, 0.95, 0], "sec": 60}}
    names = voices.identify(vecs, profiles)
    assert names == {"a": "דנה", "b": "יוסי"}                # "c" matches nobody
    profiles2 = {"דנה": {"v": [0.7, 0.7, 0], "sec": 60}}     # close to both a and b: used once
    assert len(voices.identify(vecs, profiles2)) <= 1


def test_learn_accumulates_a_running_average(tmp_path):
    import voices
    p = {}
    voices.learn(p, "דנה", [1, 0, 0], 10)
    voices.learn(p, "דנה", [0, 1, 0], 10)
    v = np.array(p["דנה"]["v"])
    assert abs(v[0] - v[1]) < 1e-3 and p["דנה"]["sec"] == 20
    voices.save_profiles(p, tmp_path / "voices.json")
    assert voices.load_profiles(tmp_path / "voices.json")["דנה"]["sec"] == 20


# ----------------------------------------------------------------------------- app integration

def _item_with_voice(root, title, vector, name="דובר 1", m=None):
    import library as lib
    import voices
    m = m or voices.VOICE_MODEL
    it = lib.Library(root).create("mic", title=title)
    it.data.update(status="done", speakers={"1": name},
                   segments=[{"start": 0, "end": 3, "speaker": "1", "text": "שלום"}],
                   voiceprints={"1": {"v": list(vector), "sec": 30, "m": m}})
    it.save()
    return it


def test_naming_a_speaker_learns_the_voice_and_names_it_elsewhere(make_window, world, msgbox, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    import voices
    root = Path(os.environ["HEBTRANSCRIBE_ROOT"])
    a = _item_with_voice(root, "פגישה א", [1, 0, 0, 0])
    b = _item_with_voice(root, "פגישה ב", [0.95, 0.1, 0, 0])      # same person
    c = _item_with_voice(root, "פגישה ג", [0, 1, 0, 0])           # someone else
    d = _item_with_voice(root, "פגישה ד", [1, 0, 0, 0], m="old")  # same voice, from an older version
    w = make_window(mic=fakes.REALTEK_MIC)
    assert wait_until(lambda: not w.processor.is_busy(), 5)
    w.select_item(a.id)
    assert w.chips_w.isVisible(), "a single speaker must still get a chip so it can be named"
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *x, **k: ("עומרי", True)))
    w.rename_speaker("1")
    assert w.items[a.id].speakers["1"] == "עומרי"
    assert "עומרי" in voices.load_profiles()
    assert w.items[b.id].speakers["1"] == "עומרי", "the same voice in another recording was not named"
    assert w.items[c.id].speakers["1"] == "דובר 1"
    assert w.items[d.id].speakers["1"] == "דובר 1", "an older kind of voiceprint was compared"
    assert "הקלטה נוספת אחת" in msgbox.texts()
    assert w.show_speakers(w.items[a.id]), "a named single speaker is shown in the transcript"


def test_giving_a_speaker_the_name_of_another_merges_them(make_window, world, msgbox, monkeypatch):
    """The app split one person in two: naming one like the other (and confirming) makes them one speaker."""
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    import library as lib
    import voices
    root = Path(os.environ["HEBTRANSCRIBE_ROOT"])
    it = lib.Library(root).create("mic", title="פגישה")
    m = voices.VOICE_MODEL
    it.data.update(status="done", speakers={"1": "דנה", "2": "דובר 2", "3": "דובר 3"},
                   segments=[{"start": 0, "end": 2, "speaker": "1", "text": "שלום"},
                             {"start": 2.5, "end": 4, "speaker": "2", "text": "מה נשמע"},
                             {"start": 5, "end": 6, "speaker": "3", "text": "טוב"}],
                   voiceprints={"1": {"v": [1, 0, 0], "sec": 10, "m": m}, "2": {"v": [0, 1, 0], "sec": 10, "m": m}})
    it.save()
    w = make_window(mic=fakes.REALTEK_MIC)
    assert wait_until(lambda: not w.processor.is_busy(), 5)
    w.select_item(it.id)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *x, **k: ("דנה", True)))
    msgbox.answer = QMessageBox.No
    w.rename_speaker("2")
    assert w.items[it.id].speakers == {"1": "דנה", "2": "דובר 2", "3": "דובר 3"}, "declined: nothing changes"
    msgbox.answer = QMessageBox.Yes
    w.rename_speaker("2")
    item = w.items[it.id]
    assert item.speakers == {"1": "דנה", "3": "דובר 3"}
    assert [(s["speaker"], s["text"]) for s in item.segments] == [("1", "שלום מה נשמע"), ("3", "טוב")]
    vp = item.data["voiceprints"]["1"]
    assert vp["sec"] == 20 and abs(vp["v"][0] - vp["v"][1]) < 1e-6
    assert "2" not in item.data["voiceprints"]


def test_new_transcription_receives_saved_voices_and_keeps_voiceprints(make_window, world, msgbox, monkeypatch, tmp_path):
    import engine
    import voices
    voices.save_profiles({"עומרי": {"v": [1, 0, 0, 0], "sec": 60, "m": voices.VOICE_MODEL}})
    seen = {}
    real = engine.Engine.process

    def proc(self, *a, **k):
        seen["profiles"] = dict(self.profiles)
        segs, spk, dur = real(self, *a, **k)
        self.last_voiceprints = {"0": {"v": [1, 0, 0, 0], "sec": 2.0, "m": voices.VOICE_MODEL}}
        return segs, {"0": "עומרי"}, dur

    monkeypatch.setattr(engine.Engine, "process", proc)
    src = tmp_path / "x.wav"
    write_wav(src, seconds=2.0)
    w = make_window(mic=fakes.REALTEK_MIC)
    w.add_files([str(src)])
    it = max(w.items.values(), key=lambda i: i.created)
    assert wait_until(lambda: it.status == "done" and not w.processor.is_busy(), 15)
    assert "עומרי" in seen["profiles"]
    assert it.speakers == {"0": "עומרי"} and it.data["voiceprints"]["0"]["sec"] == 2.0
