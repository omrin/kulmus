"""wav_to_opus() on the files an interrupted recording leaves behind."""

import struct
import wave

import numpy as np
import pytest

from conftest import ogg_info


def _tone(n, ch=1, amp=0.4):
    t = np.arange(n) / 48_000
    a = (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    return np.repeat(a[:, None], ch, axis=1)


def _pcm(a):
    return (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()


@pytest.mark.desired
def test_desired_wav_to_opus_zero_byte_wav(tmp_path):
    """A recording whose microphone never delivered a frame (the user's case) leaves a 0-byte
    recording.wav if the app dies: wave.open() raises a bare EOFError('') -> Processor stores an empty
    / meaningless error and the 0-byte WAV stays in the item folder forever.
    DESIRED: treated as an empty recording (returns 0.0) or a clear, non-empty error."""
    import recorder
    wav = tmp_path / "recording.wav"
    wav.write_bytes(b"")
    try:
        dur = recorder.wav_to_opus(wav, tmp_path / "audio.ogg")
    except Exception as e:
        assert str(e).strip(), f"wav_to_opus raised {type(e).__name__} with an EMPTY message on a 0-byte WAV"
        return
    assert dur == 0.0


def test_wav_to_opus_header_only_wav(tmp_path):
    """44-byte header, no frames (what Wave_write.close() leaves when nothing was recorded)."""
    import recorder
    wav = tmp_path / "recording.wav"
    w = wave.open(str(wav), "wb")
    w.setnchannels(1), w.setsampwidth(2), w.setframerate(48_000)
    w.close()
    assert wav.stat().st_size == 44
    ogg = tmp_path / "audio.ogg"
    dur = recorder.wav_to_opus(wav, ogg)
    assert dur == 0.0
    if ogg.exists():                      # if a file is produced it must at least be a valid Ogg
        ogg_info(ogg)


def test_wav_to_opus_recovers_crash_truncated_wav(tmp_path):
    """Crash mid-recording: header length fields still describe only the first block, the rest of the
    data is there. wav_to_opus must recover the full length (it ignores the header length fields)."""
    import recorder
    wav = tmp_path / "recording.wav"
    first, rest = _tone(4800, 2), _tone(48_000 * 2, 2)
    w = wave.open(str(wav), "wb")
    w.setnchannels(2), w.setsampwidth(2), w.setframerate(48_000)
    w.writeframes(_pcm(first))           # header now says 4800 frames
    w._file.write(_pcm(rest))            # data written later, header never patched (crash)
    w._file.write(b"\x01")               # plus a torn half-frame
    w._file.flush()
    w._file.close()
    w._file = None                       # keep Wave_write.__del__ from patching the header
    with open(wav, "rb") as f:
        hdr = f.read(44)
    assert struct.unpack("<I", hdr[40:44])[0] == 4800 * 4, "header must still claim the short length"
    ogg = tmp_path / "audio.ogg"
    dur = recorder.wav_to_opus(wav, ogg)
    expect = (4800 + 96_000) / 48_000
    assert abs(dur - expect) < 1e-6
    ch, _, odur = ogg_info(ogg)
    assert ch == 2 and abs(odur - expect) < 0.03
