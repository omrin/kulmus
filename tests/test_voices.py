"""Speaker separation refinement and recognising named voices."""

import os
from pathlib import Path

import numpy as np
import pytest

import fakes
from conftest import wait_until
from test_round2 import write_wav


class FakeEmbedder:
    """embed() returns the voice of whoever speaks at that time (vectors given per time range)."""

    def __init__(self, voices_by_time, mean=None):
        self.voices_by_time = voices_by_time       # [(start, end, vector)]
        self.mean = mean

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


def _refine(turns, by_time):
    import voices
    emb = FakeEmbedder(by_time)
    real = voices._members

    def members(audio, turns_, embedder, label):     # tell the fake embedder which turn it embeds
        out = []
        for s, e, k in turns_:
            if k == label and e - s >= voices.MIN_TURN:
                embedder._t = s
                out.append((embedder.embed(None), e - s))
        return out

    voices._members = members
    try:
        return voices.refine(np.zeros(16000 * 200, np.float32), turns, emb)
    finally:
        voices._members = real


A, B = [1, 0, 0, 0], [0, 1, 0, 0]


def test_refine_merges_a_speaker_split_in_two():
    turns = [(0, 10, 0), (10, 20, 1), (20, 30, 2), (30, 40, 1)]
    by_time = [(0, 10, A), (10, 20, B), (20, 30, [0.97, 0.05, 0, 0]), (30, 40, B)]   # 2 = same voice as 0
    new, vecs = _refine(turns, by_time)
    assert {k for _, _, k in new} == {0, 1}
    assert [k for _, _, k in new] == [0, 1, 0, 1]
    assert set(vecs) == {0, 1}


def test_refine_keeps_different_speakers_apart():
    turns = [(0, 10, 0), (10, 20, 1), (20, 30, 0)]
    new, _ = _refine(turns, [(0, 10, A), (10, 20, B), (20, 30, A)])
    assert [k for _, _, k in new] == [0, 1, 0]


def test_refine_absorbs_a_tiny_fragment_into_the_closest_voice():
    turns = [(0, 60, 0), (60, 61.2, 5), (61.2, 120, 1)]
    by_time = [(0, 60, A), (60, 61.2, [0.6, 0.4, 0, 0]), (61.2, 120, B)]
    new, vecs = _refine(turns, by_time)
    assert [k for _, _, k in new] == [0, 0, 1]
    assert 5 not in vecs


@pytest.mark.parametrize("c_secs, expected", [(10, 3), (3, 2)])
def test_a_short_distinct_voice_stays_a_speaker_but_a_blip_does_not(c_secs, expected):
    """A lecture: someone asks a short question (under 2% of the speech) in a voice unlike anyone else's."""
    C = [0, 0, 1, 0]
    half = c_secs / 2
    turns = ([(i * 10, i * 10 + 10, 0) for i in range(60)] + [(600, 600 + half, 2), (610, 610 + half, 2)]
             + [(620 + i * 10, 630 + i * 10, 1) for i in range(10)])
    by_time = [(0, 600, A), (600, 620, C), (620, 720, B)]
    new, vecs = _refine(turns, by_time)
    assert len(vecs) == expected


def _phone_like(seed, same_person):
    """Two clusters of 8 two-second turns whose centroids are ~0.75 alike, as in a phone call. Two people:
    each turn sounds like its own speaker. One person: the turns vary a lot and the split is arbitrary."""
    rng = np.random.default_rng(seed)
    common = np.zeros(16); common[0] = 1.0
    p = common + 0.6 * np.eye(16)[1]
    q = common + 0.6 * np.eye(16)[2]
    turns, by_time = [], []
    for i in range(16):
        k = i % 2
        if same_person:
            v = p + 0.45 * rng.standard_normal(16) + (0.3 * np.eye(16)[2] if k else 0)
        else:
            v = (p if k == 0 else q) + 0.05 * rng.standard_normal(16)
        turns.append((2 * i, 2 * i + 2, k))
        by_time.append((2 * i, 2 * i + 2, v))
    return turns, by_time


def _centroid_sim(turns, by_time):
    c = [np.mean([v / np.linalg.norm(v) for (s, e, k), (_, _, v) in zip(turns, by_time) if k == j], axis=0)
         for j in (0, 1)]
    return float(c[0] @ c[1] / np.linalg.norm(c[0]) / np.linalg.norm(c[1]))


def test_refine_separates_similar_but_consistent_voices_like_a_phone_call():
    turns, by_time = _phone_like(1, same_person=False)
    import voices
    assert voices.MERGE_SIM <= _centroid_sim(turns, by_time) < voices.SURE_SIM   # the case the test is about
    new, vecs = _refine(turns, by_time)
    assert len(vecs) == 2
    assert {k for _, _, k in new[0::2]} != {k for _, _, k in new[1::2]}


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_refine_keeps_one_variable_voice_as_one_speaker(seed):
    turns, by_time = _phone_like(seed, same_person=True)
    import voices
    assert voices.MERGE_SIM <= _centroid_sim(turns, by_time) < voices.SURE_SIM
    new, vecs = _refine(turns, by_time)
    assert len(vecs) == 1


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

def _item_with_voice(root, title, vector, name="דובר 1"):
    import library as lib
    it = lib.Library(root).create("mic", title=title)
    it.data.update(status="done", speakers={"1": name},
                   segments=[{"start": 0, "end": 3, "speaker": "1", "text": "שלום"}],
                   voiceprints={"1": {"v": list(vector), "sec": 30}})
    it.save()
    return it


def test_naming_a_speaker_learns_the_voice_and_names_it_elsewhere(make_window, world, msgbox, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    import voices
    root = Path(os.environ["HEBTRANSCRIBE_ROOT"])
    a = _item_with_voice(root, "פגישה א", [1, 0, 0, 0])
    b = _item_with_voice(root, "פגישה ב", [0.95, 0.1, 0, 0])      # same person
    c = _item_with_voice(root, "פגישה ג", [0, 1, 0, 0])           # someone else
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
    assert "הקלטה נוספת אחת" in msgbox.texts()
    assert w.show_speakers(w.items[a.id]), "a named single speaker is shown in the transcript"


def test_new_transcription_receives_saved_voices_and_keeps_voiceprints(make_window, world, msgbox, monkeypatch, tmp_path):
    import engine
    import voices
    voices.save_profiles({"עומרי": {"v": [1, 0, 0, 0], "sec": 60}})
    seen = {}
    real = engine.Engine.process

    def proc(self, *a, **k):
        seen["profiles"] = dict(self.profiles)
        segs, spk, dur = real(self, *a, **k)
        self.last_voiceprints = {"0": {"v": [1, 0, 0, 0], "sec": 2.0}}
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
