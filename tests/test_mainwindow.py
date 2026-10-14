"""MainWindow recording state machine (offscreen Qt, fake audio stack, stubbed transcription)."""

import json
import time

import pytest
from PySide6.QtWidgets import QMessageBox

import fakes
from conftest import ogg_info, user_was_warned, wait_until


def dev(world, name, api=fakes.MME):
    return next(d for d in world.os_devices if d.name == name and d.hostapi == api)


def ui_usable(w):
    return (w.recorder is None and w.rec_item is None and w.btn_rec.isEnabled() and w.btn_call.isEnabled()
            and w.btn_import.isEnabled() and w.list.isEnabled() and w.stack.currentIndex() != 1
            and not w.rec_timer.isActive())


def processed(w, item, timeout=15):
    """Waits until the Processor has fully finished the item (transcription AND the WAV->Opus step;
    status turns 'done' before the Opus conversion runs)."""
    return wait_until(lambda: item.status in ("done", "error") and w.processor.current is not item
                      and w.processor.jobs.empty(), timeout)


def item_folders(w):
    return [p for p in w._test_root.iterdir() if p.is_dir()]


# ----------------------------------------------------------------------------------- happy path

@pytest.mark.parametrize("first_mode", ["mic", "call"])
def test_start_stop_then_second_recording(make_window, world, msgbox, first_mode):
    world.speed = 5
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording(first_mode)
    assert w.recorder is not None and w.stack.currentIndex() == 1
    assert not w.btn_rec.isEnabled() and not w.btn_call.isEnabled()
    assert wait_until(lambda: w.recorder.seconds >= 1.5, 5), f"stuck at {w.recorder.seconds:.2f}s"
    assert wait_until(lambda: w.rec_time.text() != "00:00", 1), "timer label must advance"
    first = w.rec_item
    w.stop_recording()
    assert ui_usable(w)
    assert first.id in w.items
    assert processed(w, first) and first.status == "done", f"status={first.status} err={first.data.get('error')}"
    files = sorted(p.name for p in first.folder.iterdir())
    assert "audio.ogg" in files and "recording.wav" not in files,         f"files={files} error={first.data.get('error')!r}"
    ch, _, odur = ogg_info(first.folder / "audio.ogg")
    assert ch == (2 if first_mode == "call" else 1) and odur >= 1.5, (ch, odur, first.duration)

    w.start_recording("mic")
    assert w.recorder is not None and w.rec_item is not first, "a second recording must start"
    assert wait_until(lambda: w.recorder.seconds >= 1.2, 5), "second recording must capture audio"
    second = w.rec_item
    w.stop_recording()
    assert ui_usable(w)
    assert processed(w, second) and second.status == "done"
    assert not msgbox.kinds("warning", "critical"), msgbox.calls


# ----------------------------------------------------------------------------------- start failures

def test_start_failure_leaves_no_state_and_can_restart(make_window, world, msgbox):
    world.speed = 5
    w = make_window(mic=fakes.REALTEK_MIC)
    world.input_open_error = fakes.PortAudioError(
        "Error opening InputStream: Device unavailable [PaErrorCode -9985]")
    w.start_recording("mic")
    warns = msgbox.kinds("warning", "critical")
    assert warns, "the user must be told the recording could not start"
    detail = warns[-1][2].split("\n", 1)[-1].strip()
    assert detail, f"start-failure message has no detail: {warns[-1][2]!r}"
    assert ui_usable(w)
    assert item_folders(w) == [], f"leftover item folders: {item_folders(w)}"
    assert all(not s.active for s in world.streams), "no stream may be left running"

    world.input_open_error = None
    w.start_recording("mic")
    assert w.recorder is not None
    assert wait_until(lambda: w.recorder.seconds >= 1.2, 5)
    w.stop_recording()
    assert ui_usable(w)


def test_call_loopback_unavailable_records_mic_only_with_warning(make_window, world, msgbox):
    """soundcard can't open the computer-audio loopback (bare AssertionError for unsupported mix
    formats). The call recording must still record the microphone, and say so in readable text."""
    world.speed = 5
    w = make_window(mic=fakes.REALTEK_MIC)
    world.sc_get_microphone_error = AssertionError()        # empty message
    w.start_recording("call")
    assert w.recorder is not None, "a loopback failure must not prevent recording the microphone"
    assert wait_until(lambda: w.recorder.seconds >= 1.2, 5), "mic audio must keep being written"
    wait_until(lambda: "שמע המחשב לא נקלט" in w.rec_hint.text(), 2)
    assert "שמע המחשב לא נקלט" in w.rec_hint.text() and "AssertionError()" not in w.rec_hint.text()
    item = w.rec_item
    w.stop_recording()
    assert ui_usable(w) and item.id in w.items
    assert processed(w, item)
    assert (item.folder / "audio.ogg").exists()


@pytest.mark.desired
def test_desired_start_failure_after_wav_open_leaves_no_orphan_folder(make_window, world, msgbox):
    """InputStream.start() raising (MME 'Unanticipated host error' on a just-unplugged device) happens
    after wave.open(): library.delete() can't remove the still-open recording.wav on Windows, leaving an
    orphan folder (item.json deleted, recording.wav kept) in the library on every failed attempt."""
    w = make_window(mic=fakes.REALTEK_MIC)
    world.input_start_error = fakes.PortAudioError(
        "Error starting stream: Unanticipated host error [PaErrorCode -9999]")
    w.start_recording("mic")
    assert msgbox.kinds("warning")
    assert ui_usable(w)
    left = [(p.name, [c.name for c in p.iterdir()]) for p in item_folders(w)]
    assert left == [], f"orphan item folder(s) left in the library: {left}"


# ----------------------------------------------------------------------------------- errors mid-recording

def test_loopback_error_mid_call_keeps_recording_mic(make_window, world, msgbox):
    """The computer-audio device goes away mid-call (AUDCLNT_E_DEVICE_INVALIDATED). The microphone must
    keep recording (zero-filled system channel), the user sees a readable warning, nothing is lost."""
    world.speed = 3
    world.sc_record_error = RuntimeError("Error 0x88890004")
    world.sc_record_error_after = 1.5
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("call")
    item = w.rec_item
    assert wait_until(lambda: "שמע המחשב לא נקלט" in w.rec_hint.text(), 8), "user must be told"
    assert "נותק" in w.rec_hint.text(), w.rec_hint.text()       # HRESULT mapped to Hebrew
    assert w.recorder is not None, "the mic recording must continue"
    before = w.recorder.seconds
    assert wait_until(lambda: w.recorder.seconds >= before + 1.0, 5), "mic audio must keep being written"
    w.stop_recording()
    assert ui_usable(w)
    assert item.id in w.items and processed(w, item)
    ogg = item.folder / "audio.ogg"
    assert ogg.exists(), f"no audio.ogg; files={[p.name for p in item.folder.iterdir()]}"
    assert ogg_info(ogg)[2] >= 2.4

    world.sc_record_error = None
    msgbox.calls.clear()
    w.start_recording("call")
    assert w.recorder is not None and wait_until(lambda: w.recorder.seconds >= 1.0, 5)
    w.stop_recording()
    assert ui_usable(w)


@pytest.mark.desired
@pytest.mark.parametrize("mode", ["mic", "call"])
def test_desired_no_mic_callbacks_warns_user(make_window, world, msgbox, mode):
    """THE USER'S BUG: the mic stream opens fine but never calls back (stale MME device after a
    hot-plug). Today the screen sits at 00:00 forever with no message, the silent-mic hint never shows
    (it requires seconds > 4) and Stop silently deletes the item. DESIRED: within a few seconds the user
    is told that no audio is arriving from the microphone. (In call mode the writer only writes
    min(len(mic), len(system)) frames, so with a dead mic NOTHING reaches the disk and the timer stays
    00:00 even though the system channel is fine.)"""
    dev(world, fakes.REALTEK_MIC).signal = "dead"
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording(mode)
    assert w.recorder is not None
    baseline = w.rec_hint.text() if w.rec_hint.isVisible() else ""
    t0 = time.monotonic()
    warned = wait_until(lambda: user_was_warned(w, msgbox, baseline), 7.0)
    assert world.last_input().callbacks == 0
    assert warned, (f"{time.monotonic() - t0:.1f}s without a single mic callback: timer shows "
                    f"{w.rec_time.text()!r}, hint visible={w.rec_hint.isVisible()}, no message box")


@pytest.mark.desired
def test_desired_mic_unplugged_mid_recording_warns_and_keeps_audio(make_window, world, msgbox):
    """Device yanked mid-recording: MME just stops calling back (no error). DESIRED: the user is warned
    within a few seconds, and the audio recorded before the unplug is kept."""
    world.speed = 2
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    item = w.rec_item
    assert wait_until(lambda: w.recorder.seconds >= 1.5, 5)
    world.unplug(fakes.REALTEK_MIC)
    baseline = w.rec_hint.text() if w.rec_hint.isVisible() else ""
    frozen = w.recorder.seconds
    warned = wait_until(lambda: user_was_warned(w, msgbox, baseline), 7.0)
    if w.recorder is not None:
        w.stop_recording()
    assert item.id in w.items and item.folder.exists(), "the 1.5 s recorded before the unplug were lost"
    assert warned, f"timer frozen at {frozen:.1f}s after the mic was unplugged, nothing told the user"


@pytest.mark.parametrize("kind", ["zeros", "near-silent (USB Audio Device, peak 6e-5)"])
def test_silent_mic_shows_warning(make_window, world, msgbox, kind):
    d = dev(world, fakes.REALTEK_MIC)
    if kind == "zeros":
        d.signal = "silent"
    else:
        d.amp = 0.00006
    world.speed = 2
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    assert wait_until(lambda: user_was_warned(w, msgbox, ""), 7.0), "silent microphone not reported"
    assert w.recorder is not None and w.recorder.seconds > 3
    w.stop_recording()


@pytest.mark.desired
def test_desired_short_recording_user_is_told(make_window, world, msgbox):
    """Stop with < 1 s of audio: today the item is deleted with no feedback at all, which is exactly
    what a dead microphone looks like ('the recording just closed'). DESIRED: tell the user."""
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    item = w.rec_item
    assert wait_until(lambda: w.recorder.seconds >= 0.3, 3)
    before = len(msgbox.calls)
    w.stop_recording()
    assert ui_usable(w)
    kept = item.id in w.items and item.folder.exists()
    told = len(msgbox.calls) > before
    assert told or kept, "a recording shorter than 1 s was deleted silently"


@pytest.mark.desired
def test_desired_dead_mic_stop_user_is_told(make_window, world, msgbox):
    """User's exact flow: mic delivers nothing, user waits, presses Stop -> item vanishes silently."""
    dev(world, fakes.REALTEK_MIC).signal = "dead"
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    wait_until(lambda: False, 2.0)
    if w.recorder is not None:
        w.stop_recording()
    assert ui_usable(w)
    assert msgbox.kinds("warning", "critical", "information", "question", "exec"), \
        "2 s 'recording' with zero audio frames ended without any message"


# ----------------------------------------------------------------------------------- stop / discard / close

def test_close_during_recording_keeps_audio(make_window, world, msgbox):
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    item = w.rec_item
    assert wait_until(lambda: w.recorder.seconds >= 1.5, 5)
    w.close()
    assert msgbox.kinds("question"), "closing during a recording must ask"
    assert not w.isVisible()
    assert not w.processor.isRunning(), "processor thread must be stopped on close"
    assert all(not s.active for s in world.input_streams), "mic stream left running after close"
    assert item.folder.exists()
    data = json.loads((item.folder / "item.json").read_text("utf-8"))
    assert data["status"] in ("queued", "done"), data["status"]
    wav, ogg = item.folder / "recording.wav", item.folder / "audio.ogg"
    assert ogg.exists() or (wav.exists() and wav.stat().st_size > 44 + 48_000 * 2), "recording lost on close"


def test_close_during_recording_answer_no_keeps_recording(make_window, world, msgbox):
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    msgbox.answer = QMessageBox.StandardButton.No
    w.close()
    assert w.isVisible() and w.recorder is not None and w.rec_timer.isActive()
    s = w.recorder.seconds
    assert wait_until(lambda: w.recorder.seconds > s + 0.5, 3), "recording must continue after 'No'"
    w.stop_recording()
    assert ui_usable(w)


def test_discard_recording_removes_item_and_ui_usable(make_window, world, msgbox):
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    item = w.rec_item
    assert wait_until(lambda: w.recorder.seconds >= 1.0, 5)
    w.discard_recording()
    assert ui_usable(w) and not item.folder.exists() and item.id not in w.items
    w.start_recording("mic")
    assert w.recorder is not None
    w.stop_recording()


@pytest.mark.desired
def test_desired_stream_stop_error_does_not_wedge_ui(make_window, world, msgbox):
    """If Recorder.stop() raises (here: InputStream.stop() raising PortAudioError), _end_recording()
    never clears self.recorder, so start_recording() returns immediately forever and the record buttons
    stay disabled. (Real sounddevice ignores stop/close errors by default, so this needs an exception
    from somewhere in Recorder.stop(); _end_recording has no try/finally.)"""
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    assert wait_until(lambda: w.recorder.seconds >= 1.2, 5)
    world.input_stop_error = fakes.PortAudioError("Error stopping stream: Unanticipated host error")
    try:
        w.stop_recording()
    except Exception:
        pass
    world.input_stop_error = None
    assert w.recorder is None, "self.recorder still set -> every later start_recording() is a no-op"
    w.start_recording("mic")
    assert w.recorder is not None and wait_until(lambda: w.recorder.seconds >= 1.0, 5)
    w.stop_recording()


@pytest.mark.desired
def test_desired_error_while_discard_question_open(make_window, world, msgbox):
    """Race: the 'cancel recording?' question runs a nested event loop while rec_timer keeps ticking.
    If the recorder reports an error meanwhile, on_rec_tick() ends the recording; answering Yes then
    calls _end_recording() again -> AttributeError on None (same pattern in closeEvent)."""
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    assert wait_until(lambda: w.recorder.seconds >= 1.2, 5)

    def tick_with_error(text):
        if w.recorder is not None:
            w.recorder.error = "simulated device error"
            w.on_rec_tick()                  # what the nested event loop does

    msgbox.on_question = tick_with_error
    try:
        w.discard_recording()
    except Exception as e:
        pytest.fail(f"discard_recording raised {e!r}")
    assert ui_usable(w)


# ----------------------------------------------------------------------------------- restart recovery

@pytest.mark.desired
def test_desired_resume_interrupted_empty_recording(make_window, world, msgbox, tmp_path, monkeypatch):
    """App killed while a dead-mic recording was 'running' leaves status='recording' + 0-byte
    recording.wav. On the next start the item is processed, fails with a technical decoder error, and
    wav_to_opus() raises EOFError('') which is swallowed: the 0-byte WAV stays forever and the item is a
    permanent error with no retry. DESIRED: no 0-byte WAV left behind and a clear error (or no item)."""
    import os
    from pathlib import Path
    import library as lib
    root = Path(os.environ["HEBTRANSCRIBE_ROOT"])
    item = lib.Library(root).create("mic")
    item.data["status"] = "recording"
    item.save()
    (item.folder / "recording.wav").write_bytes(b"")
    w = make_window(mic=fakes.REALTEK_MIC)
    it = w.items[item.id]
    assert processed(w, it)
    wait_until(lambda: False, 0.3)
    leftovers = [p.name for p in it.folder.iterdir()] if it.folder.exists() else []
    print("status:", it.status, "error:", repr(it.data.get("error")), "files:", leftovers)
    assert "recording.wav" not in leftovers, (
        f"0-byte recording.wav left behind; item status={it.status!r} error={it.data.get('error')!r}")


# ----------------------------------------------------------------------------------- background processing

@pytest.mark.desired
def test_desired_recording_not_starved_by_background_transcription(make_window, world, msgbox, monkeypatch):
    """sherpa-onnx OfflineSpeakerDiarization.process() keeps the GIL (measured: ~1.9 s stall for 54 s
    of audio with the progress callback, 10.4 s without; it scales with the recording length). When it
    ran in the UI process, a recording started while the previous one was being processed lost audio
    (MME input overflow) and the UI froze. The app now runs jobs in a child process (worker.py); here
    the child's 'process' job holds ITS GIL for 2 s (tests/heavy_worker.py) while the user records
    again in the UI process. DESIRED: no microphone audio is lost."""
    import hebtranscribe as ht
    import heavy_worker
    import worker
    monkeypatch.delenv("HEBTRANSCRIBE_INPROCESS", raising=False)
    monkeypatch.setattr(ht, "make_backend",
                        lambda log_path=None: worker.SubprocessBackend(log_path, target=heavy_worker.main))
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    assert isinstance(w.processor.backend, worker.SubprocessBackend)
    w.start_recording("mic")
    assert wait_until(lambda: w.recorder.seconds >= 1.2, 4)
    w.stop_recording()                          # job for recording 1 starts in the background
    item1 = next(i for i in w.items.values())
    assert wait_until(lambda: item1.data.get("stage") == "מזהה דוברים…" or item1.status == "done", 20),         "the child process never reached the GIL-holding stage"
    world.speed = 1
    w.start_recording("mic")                    # user records again while the child holds its GIL
    assert w.recorder is not None
    wait_until(lambda: False, 4.0)
    s = world.last_input()
    rec_seconds = w.recorder.seconds
    w.stop_recording()
    assert s.dropped_frames == 0, (
        f"{s.dropped_frames / 48_000:.1f}s of microphone audio lost ({s.overflows} input overflow(s)) "
        f"while the previous recording was being processed; recorded {rec_seconds:.1f}s in ~4s")
    assert processed(w, item1, 30), "the background job did not finish"
