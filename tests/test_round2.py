"""Regression tests for the second review round (bugs found in the first set of fixes)."""

import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pytest

import fakes
from conftest import ogg_info, wait_until
from test_mainwindow import dev, item_folders, processed, ui_usable


def write_wav(path: Path, seconds=1.5, channels=1, rate=48_000, amp=0.3):
    t = np.arange(int(seconds * rate)) / rate
    mono = (amp * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    data = np.repeat(mono[:, None], channels, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def slow_wav_to_opus(monkeypatch, seconds=1.5):
    """Makes the audio-save stage take a while, like encoding a long call."""
    import recorder
    real = recorder.wav_to_opus

    def slow(*a, **k):
        time.sleep(seconds)
        return real(*a, **k)

    monkeypatch.setattr(recorder, "wav_to_opus", slow)


# ----------------------------------------------------------------------------- recording screen

def test_hint_does_not_carry_over_to_the_next_recording(make_window, world, msgbox):
    """Silent mic -> red 'no sound' hint. The user follows its advice (stop, choose another mic, start
    again): the new recording on a working mic must not keep showing the red warning."""
    world.speed = 1
    dev(world, fakes.REALTEK_MIC).signal = "silent"
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    assert wait_until(lambda: w.rec_hint.isVisible() and "לא נקלט קול" in w.rec_hint.text(), 7)
    w.stop_recording()
    w.set_mic(fakes.CAMERA_MIC)
    w.start_recording("mic")
    wait_until(lambda: False, 1.0)
    assert not (w.rec_hint.isVisible() and "לא נקלט קול" in w.rec_hint.text()), w.rec_hint.text()
    w.stop_recording()


def test_call_hint_does_not_leak_into_mic_recording(make_window, world, msgbox):
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("call")
    assert wait_until(lambda: w.rec_hint.isVisible() and "Zoom" in w.rec_hint.text(), 3)
    assert wait_until(lambda: w.recorder.seconds >= 1.0, 5)
    w.stop_recording()
    w.start_recording("mic")
    wait_until(lambda: False, 0.5)
    assert not w.rec_hint.isVisible() or "Zoom" not in w.rec_hint.text()
    w.stop_recording()


def test_call_with_mic_that_never_delivers_is_not_kept(make_window, world, msgbox):
    """Call mode, dead mic, working loopback: the writer pads the mic channel, but nothing from the
    user was ever heard -> no junk 'call' item, and the 'no audio from <mic>' explanation."""
    dev(world, fakes.REALTEK_MIC).signal = "dead"
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("call")
    assert wait_until(lambda: w.recorder is None, 8), "the watchdog must end the recording"
    assert ui_usable(w)
    assert item_folders(w) == [], [p.name for p in item_folders(w)]
    assert "לא הגיע שמע מהמיקרופון" in msgbox.texts(), msgbox.texts()
    assert "נשמר ונשלח לתמלול" not in msgbox.texts(), "contradictory message: says it was saved"


def test_loopback_reopens_after_a_failure(make_window, world, msgbox):
    """The computer-audio endpoint fails mid-call, then comes back: the rest of the call is recorded."""
    world.speed = 3
    world.sc_record_error = RuntimeError("Error 0x88890004")
    world.sc_record_error_after = 1.0
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("call")
    assert wait_until(lambda: "שמע המחשב לא נקלט" in w.rec_hint.text(), 6)
    world.sc_record_error = None                         # device is back
    assert wait_until(lambda: "שמע המחשב לא נקלט" not in w.rec_hint.text(), 6), "warning must clear"
    assert len(world.loopback_recorders) >= 2, "loopback was not re-opened"
    w.stop_recording()
    assert ui_usable(w)


# ----------------------------------------------------------------------------- audio-save stage

def test_item_stays_busy_while_its_audio_is_saved(make_window, world, msgbox, monkeypatch):
    """STORE-1/W2-1/REC2-2: during 'שומר את ההקלטה…' the item must look busy, and showing it must not
    open recording.wav in the player (that lock made the WAV delete fail with WinError 32)."""
    slow_wav_to_opus(monkeypatch, 1.5)
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    assert wait_until(lambda: w.recorder.seconds >= 1.2, 5)
    w.stop_recording()
    item = w.current
    assert wait_until(lambda: item.data.get("stage") == "שומר את ההקלטה…", 10)
    assert item.status == "processing" and w.busy(item)
    w.show_item(None)
    w.select_item(item.id)                                # the user clicks the item again
    assert w.player.source().isEmpty(), f"player opened {w.player.source().toLocalFile()} while saving"
    assert not w.act_redo.isEnabled() and not w.act_del_audio.isEnabled()
    assert processed(w, item)
    assert item.status == "done" and not item.data.get("warning"), item.data.get("warning")
    assert (item.folder / "audio.ogg").exists() and not (item.folder / "recording.wav").exists()


def test_retry_pressed_while_item_is_still_saving_is_not_lost(make_window, world, msgbox, monkeypatch):
    """W2-2: a request for the item the processor still owns is run afterwards, not silently dropped."""
    import engine
    calls = []
    real = engine.Engine.process

    def counting(self, *a, **k):
        calls.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(engine.Engine, "process", counting)
    slow_wav_to_opus(monkeypatch, 1.5)
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    assert wait_until(lambda: w.recorder.seconds >= 1.2, 5)
    w.stop_recording()
    item = w.current
    assert wait_until(lambda: item.data.get("stage") == "שומר את ההקלטה…", 10)
    w.processor.enqueue(item)                             # e.g. "תמלול מחדש" right now
    assert wait_until(lambda: len(calls) >= 2, 15), "the second request was dropped"
    assert processed(w, item)


def test_cancelled_audio_only_job_does_not_swallow_a_later_retranscription(make_window, world, msgbox, monkeypatch):
    """STORE-5/W2-6: a cancelled queued 'save audio' job must not turn the next re-transcription into
    an audio-only job."""
    import engine
    calls = []
    real = engine.Engine.process

    def slow_counting(self, *a, **k):
        calls.append(1)
        time.sleep(1.0)
        return real(self, *a, **k)

    monkeypatch.setattr(engine.Engine, "process", slow_counting)
    root = Path(os.environ["HEBTRANSCRIBE_ROOT"])
    import library as lib
    lib_ = lib.Library(root)
    busy_item = lib_.create("file", title="busy")
    write_wav(busy_item.folder / "recording.wav")
    busy_item.data["status"] = "queued"
    busy_item.save()
    target = lib_.create("mic", title="target")
    write_wav(target.folder / "recording.wav")
    target.data.update(status="done", segments=[{"start": 0, "end": 1, "speaker": "0", "text": "ישן"}],
                       speakers={"0": "דובר 1"})
    target.save()
    w = make_window(mic=fakes.REALTEK_MIC)
    t = w.items[target.id]
    b = w.items[busy_item.id]
    assert wait_until(lambda: w.processor.current is b, 5)
    assert t.data.get("finalize_only"), "resume should have queued an audio-only job for the target"
    w.select_item(t.id)
    w.banner_action()                                     # cancel the waiting audio-only job
    assert t.status == "done" and not t.data.get("finalize_only")
    assert processed(w, b, 20)
    calls.clear()
    w.processor.enqueue(t)                                # "תמלול מחדש…"
    assert processed(w, t, 20)
    assert calls, "re-transcription ran as an audio-only job"


def test_no_speech_recording_stays_done_after_audio_save(make_window, world, msgbox):
    """STORE-6/W2-5: an interrupted audio save of a 'no speech' recording must not turn it into an
    ERROR with an empty message."""
    root = Path(os.environ["HEBTRANSCRIBE_ROOT"])
    import library as lib
    it = lib.Library(root).create("mic")
    write_wav(it.folder / "recording.wav", amp=0.0)
    it.data.update(status="done", segments=[], speakers={})
    it.save()
    w = make_window(mic=fakes.REALTEK_MIC)
    item = w.items[it.id]
    assert processed(w, item)
    assert item.status == "done" and not item.data.get("error"), (item.status, item.data.get("error"))
    assert (item.folder / "audio.ogg").exists() and not (item.folder / "recording.wav").exists()


def test_interrupted_import_copy_is_resumed(make_window, world, msgbox, tmp_path):
    """STORE-4/W2-3: transcribed import whose library copy was interrupted -> redone on next start."""
    src = tmp_path / "source.wav"
    write_wav(src, seconds=2.0)
    root = Path(os.environ["HEBTRANSCRIBE_ROOT"])
    import library as lib
    it = lib.Library(root).create("file", title="source", original_name="source.wav")
    it.data.update(status="done", pending_source=str(src),
                   segments=[{"start": 0, "end": 2, "speaker": "0", "text": "בדיקה"}], speakers={"0": "דובר 1"})
    it.save()
    w = make_window(mic=fakes.REALTEK_MIC)
    item = w.items[it.id]
    assert processed(w, item)
    assert (item.folder / "audio.ogg").exists(), [p.name for p in item.folder.iterdir()]
    assert not item.data.get("pending_source") and item.status == "done"
    assert ogg_info(item.folder / "audio.ogg")[2] >= 1.9


# ----------------------------------------------------------------------------- misc

def test_select_item_hidden_by_search_clears_the_search(make_window, world, msgbox, tmp_path):
    src = tmp_path / "zzz.wav"
    write_wav(src)
    w = make_window(mic=fakes.REALTEK_MIC)
    w.search.setText("שום-דבר-תואם")
    w.add_files([str(src)])
    new = max(w.items.values(), key=lambda i: i.created)
    assert w.search.text() == ""
    cur = w.list.currentItem()
    assert cur is not None and cur.data(256) == new.id   # Qt.UserRole


def test_refresh_devices_recovers_after_a_failed_reinit(world):
    """REC2-5: after one failed Pa_Initialize, the next refresh must not fail on Pa_Terminate."""
    import recorder
    world.refcount = 0                                    # PortAudio left uninitialised
    assert recorder.refresh_devices() is True
    assert world.refcount == 1


def test_load_all_tolerates_files_held_by_another_process(tmp_path):
    """STORE-3/W2-4: a .part still held open (orphan encoder) must not stop the app from starting."""
    import library as lib
    L = lib.Library(tmp_path)
    it = L.create("mic")
    part = it.folder / "audio.ogg.part"
    with open(part, "wb") as held:
        held.write(b"x" * 100)
        items = L.load_all()                              # must not raise
    assert [i.id for i in items] == [it.id]


def test_wav_header_is_clamped_beyond_4gib(tmp_path):
    """REC2-4: the 'wave' module raises struct.error past 4 GiB (a ~6 h call); the writer clamps."""
    import recorder
    w = recorder._WavFile(tmp_path / "r.wav", 2, 48_000)
    w.data = 0xFFFFFFFF - 10                              # pretend 6+ hours were already written
    w.write(b"\x00" * 400)
    w.close()
    raw = (tmp_path / "r.wav").read_bytes()[:44]
    assert raw[4:8] == b"\xff\xff\xff\xff" and raw[40:44] == b"\xff\xff\xff\xff"
    with wave.open(str(tmp_path / "r.wav"), "rb") as r:
        assert r.getnchannels() == 2 and r.getframerate() == 48_000


def test_file_without_audio_track_is_a_permanent_readable_error(tmp_path):
    """W2-7: a muted video -> 'בקובץ אין ערוץ שמע', marked permanent (retry hidden)."""
    import av
    import engine
    import worker
    vid = tmp_path / "muted.mkv"
    c = av.open(str(vid), "w")
    s = c.add_stream("mpeg4", rate=10)
    s.width, s.height, s.pix_fmt = 64, 64, "yuv420p"
    for i in range(5):
        fr = av.VideoFrame.from_ndarray(np.zeros((64, 64, 3), np.uint8), format="rgb24")
        for p in s.encode(fr):
            c.mux(p)
    for p in s.encode(None):
        c.mux(p)
    c.close()
    with pytest.raises(engine.NoAudio):
        engine.load_channels(vid)
    b = worker.InProcessBackend()
    with pytest.raises(worker.JobError) as ei:
        b.call("convert", {"src": str(vid), "out": str(tmp_path / "o.ogg")}, lambda *a: None, lambda *a: None,
               lambda: False)
    assert ei.value.permanent and "אין ערוץ שמע" in str(ei.value)


def test_single_instance_takes_over_a_crash_left_lock(qapp, tmp_path):
    """STORE-8: lock file left by a crash whose PID now belongs to another python process (here: us)."""
    import hebtranscribe as ht
    (tmp_path / "app.lock").write_text(f"{os.getpid()}\npython\n{os.environ.get('COMPUTERNAME', '')}\n")
    inst = ht.single_instance(tmp_path)
    assert inst is not None, "app refused to start although no instance answers"
    lock, server = inst
    server.close()
    lock.unlock()


PARENT = r'''
import sys, time
sys.path.insert(0, {app!r})
def main():
    import worker
    b = worker.SubprocessBackend(None)
    b._ensure()
    print(b.proc.pid, flush=True)
    time.sleep(60)
if __name__ == "__main__":
    main()
'''


def _alive(pid):
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == 259                          # STILL_ACTIVE


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handling")
def test_worker_child_dies_with_a_killed_app(tmp_path):
    """W2-4: the processing child must not outlive an app that crashed / was killed."""
    script = tmp_path / "parent.py"
    script.write_text(PARENT.format(app=str(Path(__file__).resolve().parent.parent)), encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    try:
        child = int(p.stdout.readline())
        assert wait_until(lambda: _alive(child), 10)
        subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True)
        assert wait_until(lambda: not _alive(child), 10), "worker child kept running after the app died"
    finally:
        if p.poll() is None:
            p.kill()


# ============================================================================= round 3

def test_saving_stage_persists_transcript_and_resumes_audio_only(make_window, world, msgbox, monkeypatch):
    """R3-2: a UI save (rename) during the audio-save stage, then a crash: the next start must only
    redo the encode, not re-transcribe."""
    import json
    slow_wav_to_opus(monkeypatch, 1.5)
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    assert wait_until(lambda: w.recorder.seconds >= 1.2, 5)
    w.stop_recording()
    item = w.current
    assert wait_until(lambda: item.data.get("stage") == "שומר את ההקלטה…", 10)
    w.title_edit.setText("פגישה חשובה")
    w.rename_item()                                      # UI save mid-stage
    on_disk = json.loads((item.folder / "item.json").read_text(encoding="utf-8"))
    assert on_disk["finalize_only"] is True and on_disk["segments"], on_disk
    assert "partial" not in on_disk
    # the banner must not offer a 'cancel' that does nothing; the finished transcript can be copied
    assert wait_until(lambda: not w.banner_btn.isVisible() or w.banner_btn.text() != "ביטול", 2)
    assert wait_until(lambda: w.btn_copy.isEnabled(), 2)
    assert processed(w, item)


def test_crash_during_audio_save_resumes_without_retranscribing(make_window, world, msgbox, monkeypatch):
    import engine
    calls = []
    real = engine.Engine.process
    monkeypatch.setattr(engine.Engine, "process", lambda self, *a, **k: (calls.append(1), real(self, *a, **k))[1])
    root = Path(os.environ["HEBTRANSCRIBE_ROOT"])
    import library as lib
    it = lib.Library(root).create("mic")
    write_wav(it.folder / "recording.wav")
    it.data.update(status="processing", finalize_only=True, prev_status="done",
                   segments=[{"start": 0, "end": 1, "speaker": "0", "text": "קיים"}], speakers={"0": "דובר 1"})
    it.save()
    w = make_window(mic=fakes.REALTEK_MIC)
    item = w.items[it.id]
    assert processed(w, item)
    assert not calls, "the recording was re-transcribed"
    assert item.status == "done" and item.segments[0]["text"] == "קיים"
    assert (item.folder / "audio.ogg").exists() and not (item.folder / "recording.wav").exists()


def test_user_cancelled_import_copy_is_not_resumed(make_window, world, msgbox, tmp_path):
    """R3-4: an import copy the user cancelled stays cancelled across restarts; the manual retry works."""
    src = tmp_path / "source.wav"
    write_wav(src, seconds=2.0)
    root = Path(os.environ["HEBTRANSCRIBE_ROOT"])
    import library as lib
    it = lib.Library(root).create("file", title="source")
    it.data.update(status="done", pending_source=str(src), copy_cancelled=True,
                   warning="עותק השמע לא נשמר בספרייה (בוטל).",
                   segments=[{"start": 0, "end": 2, "speaker": "0", "text": "בדיקה"}], speakers={"0": "דובר 1"})
    it.save()
    w = make_window(mic=fakes.REALTEK_MIC)
    item = w.items[it.id]
    wait_until(lambda: False, 1.0)
    assert not w.processor.is_busy() and not (item.folder / "audio.ogg").exists()
    w.select_item(item.id)
    assert w.banner_btn.isVisible() and "ניסיון חוזר" in w.banner_btn.text()
    w.banner_action()
    assert processed(w, item)
    assert (item.folder / "audio.ogg").exists() and not item.data.get("copy_cancelled")


def test_mic_overflow_is_filled_with_silence(world, tmp_path):
    """REC2-3: mic audio lost to an input overflow (the process was busy) is replaced by silence, so the
    recording keeps real time instead of shifting everything after it."""
    import ctypes
    import recorder
    hold_gil = ctypes.PyDLL("kernel32").Sleep
    world.speed = 1
    rec = recorder.Recorder("mic", tmp_path / "r.wav", mic_name=fakes.REALTEK_MIC)
    t0 = time.monotonic()
    rec.start()
    time.sleep(0.6)
    hold_gil(900)                                        # nothing in this process runs for 0.9 s
    time.sleep(1.0)
    wall = time.monotonic() - t0
    rec.stop()
    s = world.last_input()
    assert s.overflows >= 1 and s.dropped_frames > 0, "the fake did not drop anything"
    assert rec.seconds >= wall - 0.35, f"recorded {rec.seconds:.2f}s of {wall:.2f}s: lost audio not filled"


def test_late_loopback_start_is_aligned(world, tmp_path, monkeypatch):
    """REC2-3: the computer-audio channel starts 0.5 s late -> it is placed 0.5 s into the file (not at 0)."""
    import recorder
    sc = sys.modules["soundcard"]
    real_get = sc.get_microphone

    def late(*a, **k):
        time.sleep(0.5)
        return real_get(*a, **k)

    monkeypatch.setattr(sc, "get_microphone", late)
    world.speed = 1
    rec = recorder.Recorder("call", tmp_path / "r.wav", mic_name=fakes.REALTEK_MIC)
    rec.start()
    time.sleep(2.0)
    rec.stop()
    with wave.open(str(tmp_path / "r.wav"), "rb") as r:
        a = np.frombuffer(r.readframes(r.getnframes()), "<i2").reshape(-1, 2)
    first_sys = int(np.argmax(np.abs(a[:, 1]) > 100)) / 48_000
    first_mic = int(np.argmax(np.abs(a[:, 0]) > 100)) / 48_000
    assert first_mic < 0.05, first_mic
    assert 0.35 <= first_sys <= 0.75, f"computer audio starts at {first_sys:.2f}s in the file (expected ~0.5)"


HOLDER = r'''
import sys, time
sys.path.insert(0, {app!r})
sys.path.insert(0, {tests!r})
def main():
    import worker, heavy_worker
    b = worker.SubprocessBackend(None, target=heavy_worker.main)
    b._ensure()
    print(b.proc.pid, flush=True)
    b.conn.send(("process", {{}}))      # the child holds its GIL for 2 s
    time.sleep(60)
if __name__ == "__main__":
    main()
'''


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_worker_child_dies_with_app_even_while_holding_its_gil(tmp_path):
    """W2-4 residual: during diarization the child's own watcher thread can't run (GIL held). The job
    object makes Windows kill it anyway when the app dies."""
    script = tmp_path / "holder.py"
    here = Path(__file__).resolve().parent
    script.write_text(HOLDER.format(app=str(here.parent), tests=str(here)), encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    try:
        child = int(p.stdout.readline())
        time.sleep(0.8)                                  # the child is inside its 2 s GIL hold now
        subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True)
        t = time.monotonic()
        assert wait_until(lambda: not _alive(child), 5)
        assert time.monotonic() - t < 1.0, "child survived until its GIL hold ended"
    finally:
        if p.poll() is None:
            p.kill()


def test_waiting_item_says_what_it_is_waiting_for(make_window, world, msgbox, monkeypatch, tmp_path):
    """A recording queued behind other work must not just say 'ממתין בתור…' for a minute."""
    import engine
    real = engine.Engine.process

    def slow(self, *a, **k):
        time.sleep(4)                    # long enough to observe the queue even on a busy machine
        return real(self, *a, **k)

    monkeypatch.setattr(engine.Engine, "process", slow)
    srcs = []
    for n in range(2):
        f = tmp_path / f"f{n}.wav"
        write_wav(f)
        srcs.append(str(f))
    w = make_window(mic=fakes.REALTEK_MIC)
    w.add_files(srcs)
    first = min(w.items.values(), key=lambda i: i.created)
    last = max(w.items.values(), key=lambda i: i.created)
    assert wait_until(lambda: w.processor.current is first, 15)
    w.select_item(last.id)
    assert wait_until(lambda: "לפניו עוד פריט אחד" in w.banner_text.text(), 10), w.banner_text.text()
    assert first.title in w.banner_text.text()
    assert processed(w, last, 20)


def test_recording_hint_renders_entities(make_window, world, msgbox):
    """html.escape turns the apostrophe in "וכו'" into &#x27;; the label must render it, not show it."""
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("call")
    assert wait_until(lambda: w.rec_hint.isVisible(), 2)
    from PySide6.QtCore import Qt
    assert w.rec_hint.textFormat() == Qt.RichText
    from PySide6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setHtml(w.rec_hint.text())
    assert "&#x27;" not in doc.toPlainText() and "וכו'" in doc.toPlainText()
    w.stop_recording()
