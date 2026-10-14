"""Recorder (recorder.py) against the fake sounddevice / soundcard stack."""

import os
import time
import wave

import numpy as np
import pytest

import fakes
from conftest import ogg_info, read_wav, wait_until


def _record(rec, seconds, timeout=10):
    rec.start()
    try:
        assert wait_until(lambda: rec.seconds >= seconds or rec.error, timeout), \
            f"recorder stuck at {rec.seconds:.2f}s (error={rec.error!r})"
    finally:
        rec.stop()


# ----------------------------------------------------------------------------------- 1. mic happy path

def test_mic_happy_path_writes_wav_and_opus(world, tmp_path):
    import recorder
    world.speed = 5
    wav = tmp_path / "recording.wav"
    rec = recorder.Recorder("mic", wav, mic_name=fakes.REALTEK_MIC)
    _record(rec, 2.0)

    assert rec.error == ""
    s = world.last_input()
    assert s.device.name == fakes.REALTEK_MIC and s.device.hostapi == fakes.MME and not s.stale
    assert s.closed, "mic stream must be closed after stop()"
    assert rec.seconds >= 2.0
    a, rate = read_wav(wav)
    assert rate == 48_000 and a.shape[1] == 1
    assert len(a) == rec.frames, "WAV frame count must equal Recorder.frames"
    assert 0.45 < np.abs(a).max() < 0.55, "mic signal (amp 0.5) must reach the file"

    ogg = tmp_path / "audio.ogg"
    dur = recorder.wav_to_opus(wav, ogg)
    assert not wav.exists(), "wav_to_opus must delete the WAV"
    assert abs(dur - rec.frames / 48_000) < 1e-6
    ch, orate, odur = ogg_info(ogg)
    assert ch == 1 and orate == 48_000
    assert abs(odur - dur) < 0.03, f"ogg decodes to {odur:.3f}s, expected {dur:.3f}s"


# ----------------------------------------------------------------------------------- 2. call mode

def test_call_happy_path_two_channels(world, tmp_path):
    import recorder
    world.speed = 5
    wav = tmp_path / "recording.wav"
    rec = recorder.Recorder("call", wav, mic_name=fakes.REALTEK_MIC)
    _record(rec, 2.0)

    assert rec.error == ""
    keep = world.output_streams
    assert keep and keep[-1].started and keep[-1].closed, "silent keep-alive OutputStream must run and be closed"
    assert world.loopback_recorders and world.loopback_recorders[-1].exited, "loopback recorder must be closed"
    a, rate = read_wav(wav)
    assert a.shape[1] == 2 and len(a) == rec.frames
    # left = mic (amp 0.5), right = system (amp 0.3)
    body = a[: int(len(a) * 0.9)]
    assert 0.45 < np.abs(body[:, 0]).max() < 0.55, "left channel must be the microphone"
    assert 0.25 < np.abs(body[:, 1]).max() < 0.35, "right channel must be the system audio"

    ogg = tmp_path / "audio.ogg"
    dur = recorder.wav_to_opus(wav, ogg)
    ch, _, odur = ogg_info(ogg)
    assert ch == 2 and abs(odur - dur) < 0.03


def test_call_loopback_assertion_error_is_surfaced_with_message(world, tmp_path):
    """soundcard's _AudioClient asserts on non-float mix formats -> bare AssertionError() (str == '').
    The microphone keeps recording; a readable, non-empty warning explains the missing computer audio."""
    import recorder
    world.speed = 3
    world.sc_recorder_error = AssertionError()
    rec = recorder.Recorder("call", tmp_path / "recording.wav", mic_name=fakes.REALTEK_MIC)
    rec.start()
    try:
        assert wait_until(lambda: rec.warning, 2.0), "loopback failure must set Recorder.warning"
        assert wait_until(lambda: rec.seconds >= 1.0, 3.0), "the mic must keep recording"
        assert not rec.error
    finally:
        rec.stop()
    msg = rec.warning
    assert msg.strip() and "AssertionError()" not in msg, f"unreadable message {msg!r}"
    assert not world.last_input().active, "mic stream must be stopped after stop()"
    assert all(s.closed for s in world.output_streams), "keep-alive must be closed after stop()"


# ----------------------------------------------------------------------------------- robustness

@pytest.mark.desired
def test_desired_writer_failure_is_reported(world, tmp_path, monkeypatch):
    """If writing the WAV fails (disk full, file locked by AV/OneDrive...), the writer thread dies with
    an uncaught exception: Recorder.error stays '' and the timer silently freezes; everything recorded
    after that point is lost. DESIRED: Recorder.error is set so the UI stops and keeps what it has."""
    import recorder
    world.speed = 5
    real_write = recorder._WavFile.write
    calls = {"n": 0}

    def failing_write(self, pcm):
        calls["n"] += 1
        if calls["n"] > 5:
            raise OSError(28, "No space left on device")
        real_write(self, pcm)

    monkeypatch.setattr(recorder._WavFile, "write", failing_write)
    rec = recorder.Recorder("mic", tmp_path / "recording.wav", mic_name=fakes.REALTEK_MIC)
    rec.start()
    try:
        ok = wait_until(lambda: rec.error, 3.0)
        frozen_at = rec.seconds
    finally:
        rec.stop()
    assert ok, f"writer died silently: Recorder.error is empty, timer frozen at {frozen_at:.2f}s"
    assert "אין מספיק מקום" in rec.error, rec.error            # OSError 28 in readable Hebrew


@pytest.mark.desired
def test_desired_failed_start_releases_wav_file(world, tmp_path):
    """InputStream.start() failing (e.g. 'Unanticipated host error' from a just-unplugged MME device)
    happens AFTER wave.open(): the WAV handle is never closed, so MainWindow's library.delete(item)
    (shutil.rmtree, ignore_errors) cannot remove the folder on Windows -> orphan folder + leaked handle."""
    import recorder
    world.input_start_error = fakes.PortAudioError(
        "Error starting stream: Unanticipated host error [PaErrorCode -9999]")
    wav = tmp_path / "item" / "recording.wav"
    wav.parent.mkdir()
    rec = recorder.Recorder("mic", wav, mic_name=fakes.REALTEK_MIC)
    with pytest.raises(fakes.PortAudioError):
        rec.start()
    rec.stop()          # what MainWindow.start_recording does on failure
    try:
        if wav.exists():
            os.remove(wav)
    except PermissionError as e:
        pytest.fail(f"recording.wav is still open after a failed start(): {e}")
