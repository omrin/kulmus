"""Microphone / call recording.

Call mode records the selected microphone (you) and a WASAPI loopback of the default speaker
(everyone else in Zoom/Teams/Meet/...) into the two channels of one file: left = mic, right = system.
Audio is streamed to a WAV file while recording (survives a crash) and encoded to Opus afterwards.
"""

import os
import queue
import struct
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

RATE = 48_000
BLOCK = RATE // 10      # 100 ms
WAV_HEADER = 44
ALIGN_GAP = 0.3         # s: a channel this far behind, and stalled, is padded with silence
STALL = 0.35            # s without a block = the channel is stalled
MAX_SKEW = 1.0          # s: never let the channels drift further apart than this

def _use_bundled_portaudio():
    """Linux builds ship libportaudio, but sounddevice only looks for a system copy (via find_library).
    Point it at the bundled one when the system has none. Must run before `import sounddevice`."""
    if not (sys.platform.startswith("linux") and getattr(sys, "frozen", False)):
        return
    import ctypes.util
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "libportaudio.so.2"
    if not bundled.exists():
        return
    original = ctypes.util.find_library

    def find_library(name):
        found = original(name)
        return str(bundled) if found is None and name == "portaudio" else found

    ctypes.util.find_library = find_library


_use_bundled_portaudio()


def _sd():
    """Imports sounddevice. On Linux, PortAudio aborts the whole process (no Python exception) when the
    ALSA sound system isn't installed at all, so check for it first and fail with a readable error."""
    if sys.platform.startswith("linux") and not os.environ.get("ALSA_CONFIG_PATH")             and not Path("/usr/share/alsa/alsa.conf").exists():
        raise OSError("מערכת השמע של Linux ‏(ALSA) לא מותקנת. התקינו את החבילה libasound2 ונסו שוב.")
    import sounddevice
    return sounddevice

# PortAudio must not be re-initialised while a stream is open (Pa_Terminate closes every stream).
_active = 0
_active_lock = threading.Lock()


def refresh_devices() -> bool:
    """Re-enumerates audio devices.

    PortAudio takes a snapshot of the devices when it is initialised (at `import sounddevice`). After a
    microphone is plugged in or out, or the Windows default changes, the old indices point at a
    different or a dead device: the stream opens fine but never delivers audio. Re-initialising takes a
    fresh snapshot. Skipped (returns False) while a recording is running.
    """
    sd = _sd()
    with _active_lock:
        if _active:
            return False
        if getattr(sd, "_initialized", 1):
            try:
                sd._terminate()
            except Exception:
                pass        # e.g. a previous re-init failed: only an *initialise* error matters
        sd._initialize()
    return True


def _input_devices():
    sd = _sd()
    api = sd.default.hostapi
    for i, d in enumerate(sd.query_devices()):
        if d["hostapi"] == api and d["max_input_channels"] > 0:
            yield i, d


def list_inputs(refresh: bool = True) -> list[str]:
    """Microphones on the default host API (MME on Windows: it resamples, so any device works at 48 kHz)."""
    if refresh:
        refresh_devices()
    names = []
    for _, d in _input_devices():
        if "Sound Mapper" not in d["name"] and "ממפה הקול" not in d["name"] and d["name"] not in names:
            names.append(d["name"])
    return names


def find_input(name: str | None):
    """Device index for a saved microphone name, or None (= Windows default) if it isn't connected."""
    if not name:
        return None
    for i, d in _input_devices():
        if d["name"] == name:
            return i
    return None


def default_input_name() -> str:
    sd = _sd()
    try:
        return sd.query_devices(kind="input")["name"]
    except Exception:
        return "ברירת המחדל של Windows"


def friendly_error(e: BaseException) -> str:
    """Readable Hebrew text for audio errors (soundcard raises bare AssertionErrors and raw HRESULTs)."""
    s = str(e).strip()
    low = s.lower()
    known = {
        "0x88890004": "התקן השמע נותק או הוחלף",
        "0x8889000a": "התקן השמע תפוס על ידי אפליקציה אחרת",
        "0x88890008": "פורמט השמע של ההתקן אינו נתמך",
        "0x80070005": "אין הרשאה למיקרופון (הגדרות הפרטיות של Windows)",
    }
    for code, msg in known.items():
        if code in low:
            return msg
    if "querying device -1" in low or "no default input device" in low or "invalid device" in low:
        return "לא נמצא מיקרופון במחשב. חברו מיקרופון או בחרו אחר בתחתית הרשימה"
    if "portaudio" in low and "initializ" in low:
        return "מערכת השמע לא אותחלה — נסו שוב"
    if isinstance(e, AssertionError):
        return "פורמט השמע של ההתקן אינו נתמך"
    if isinstance(e, OSError) and getattr(e, "winerror", None) in (32, 33, 5):
        return "הקובץ בשימוש על ידי תוכנה אחרת"
    if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
        return "אין מספיק מקום פנוי בדיסק"
    if not s:
        return e.__class__.__name__
    return s


def wav_has_audio(path: Path) -> bool:
    try:
        return path.stat().st_size > WAV_HEADER
    except OSError:
        return False


def _silence(outdata, *args):
    outdata.fill(0)


class _WavFile:
    """Minimal PCM16 WAV writer. Unlike the `wave` module it never fails past 4 GiB (a ~6 h stereo call):
    the size fields are clamped to 0xFFFFFFFF, which FFmpeg reads as 'until end of file'. The header is
    written immediately and patched after every block, so a crash leaves a readable file."""

    def __init__(self, path: Path, channels: int, rate: int):
        self.channels, self.rate = channels, rate
        self.data = 0
        self.f = open(path, "wb")
        self.f.write(self._header())
        self.f.flush()

    def _header(self) -> bytes:
        ch, rate = self.channels, self.rate
        if self.data > 0xFFFFFFFF - 36:
            riff = data = 0xFFFFFFFF
        else:
            riff, data = self.data + 36, self.data
        return (b"RIFF" + struct.pack("<I", riff) + b"WAVE"
                + b"fmt " + struct.pack("<IHHIIHH", 16, 1, ch, rate, rate * ch * 2, ch * 2, 16)
                + b"data" + struct.pack("<I", data))

    def write(self, pcm: bytes):
        self.f.write(pcm)
        self.data += len(pcm)
        end = self.f.tell()
        self.f.seek(0)
        self.f.write(self._header())
        self.f.seek(end)
        self.f.flush()

    def close(self):
        if self.f is not None and not self.f.closed:
            self.f.close()


class Recorder:
    def __init__(self, mode: str, wav_path: Path, mic_name: str | None = None):
        self.mode = mode                      # "mic" | "call"
        self.mic_name = mic_name              # None = Windows default input
        self.channels = 2 if mode == "call" else 1
        self.wav_path = wav_path
        self.levels = [0.0, 0.0]              # mic, system (0..1)
        self.last_data: list[float | None] = [None, None]   # monotonic time of the last block per channel
        self.frames = 0
        self.error = ""                       # fatal: the recording cannot continue
        self.warning = ""                     # non-fatal, e.g. the computer audio of a call isn't captured
        self.overflows = 0                    # PortAudio input overflows (audio lost because we were late)
        self.device_name = ""                 # microphone actually opened
        self.mic_missing = False              # the saved microphone isn't connected -> default was used
        self.mic_finished = False             # PortAudio ended the mic stream (unplugged / aborted)
        self.started_at: float | None = None
        self.first_data: list[float | None] = [None, None]  # capture start of the first block per channel
        self._mic_t_last: float | None = None
        self._stop = threading.Event()
        self._queues = [queue.Queue() for _ in range(self.channels)]
        self._mic = self._keepalive = self._wav = None
        self._loop_thread = self._writer_thread = None
        self._registered = False

    @property
    def seconds(self) -> float:
        return self.frames / RATE

    def mic_alive(self) -> bool:
        return self._mic is not None and not self.mic_finished and bool(getattr(self._mic, "active", True))

    # ------------------------------------------------------------------ start / stop
    def start(self):
        global _active
        sd = _sd()
        refresh_devices()
        with _active_lock:
            _active += 1
            self._registered = True
        try:
            device = find_input(self.mic_name)
            self.mic_missing = bool(self.mic_name) and device is None
            self.device_name = self.mic_name if device is not None else default_input_name()
            self._mic = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK,
                                       device=device, latency="high", callback=self._mic_callback,
                                       finished_callback=self._mic_done)
            if self.mode == "call":
                try:
                    # WASAPI loopback delivers nothing while the output is idle; playing silence keeps it
                    # flowing, so both channels stay aligned in time.
                    self._keepalive = sd.OutputStream(samplerate=RATE, channels=1, dtype="float32",
                                                      callback=_silence)
                    self._keepalive.start()
                except Exception as e:
                    print(f"keepalive output failed: {e!r}", flush=True)
                    self._keepalive = None
            self._mic.start()
            self.started_at = time.monotonic()
            self._wav = _WavFile(self.wav_path, self.channels, RATE)
            if self.mode == "call":
                self._loop_thread = threading.Thread(target=self._capture_loopback, daemon=True)
                self._loop_thread.start()
            self._writer_thread = threading.Thread(target=self._writer, daemon=True)
            self._writer_thread.start()
        except BaseException:
            self.stop()
            raise

    def stop(self):
        """Stops capturing and waits for the writer to flush and close the WAV. Safe to call twice."""
        global _active
        self._stop.set()
        for name in ("_mic", "_keepalive"):
            s = getattr(self, name)
            if s is not None:
                setattr(self, name, None)
                for op in (s.stop, s.close):
                    try:
                        op()
                    except Exception:
                        pass
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=3)
        if self._writer_thread is not None:
            # the writer keeps well under a second of audio buffered, so the final flush is quick
            self._writer_thread.join(timeout=15)
        elif self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None
        if self._registered:
            with _active_lock:
                _active -= 1
            self._registered = False

    # ------------------------------------------------------------------ capture
    def _mic_callback(self, indata, frames, time_info, status):
        now = time.monotonic()
        if getattr(status, "input_overflow", False):
            # Audio was lost because we were late. Insert the missing time as silence, so the rest of
            # the recording stays in time (in a call, aligned with the computer-audio channel).
            self.overflows += 1
            if self._mic_t_last is not None:
                lost = int((now - self._mic_t_last - frames / RATE) * RATE)
                if lost > BLOCK // 2:
                    self._queues[0].put(np.zeros(min(lost, 30 * RATE), np.float32))
        self._mic_t_last = now
        self._push(0, indata[:, 0].copy())

    def _mic_done(self):
        self.mic_finished = True

    def _push(self, idx, data):
        now = time.monotonic()
        if self.first_data[idx] is None:
            self.first_data[idx] = now - len(data) / RATE
        self.last_data[idx] = now
        self.levels[idx] = max(float(np.abs(data).max(initial=0.0)), self.levels[idx] * 0.7)
        self._queues[idx].put(data)

    def _loopback_failed(self, e):
        # the microphone keeps recording; the writer fills the system channel with silence meanwhile
        self.warning = f"שמע המחשב לא נקלט ({friendly_error(e)}) — מוקלט רק המיקרופון."

    def _capture_loopback(self):
        """Captures the default speaker. Re-opens it when it fails or when Windows switches the default
        output (headset plugged in, Bluetooth reconnect), so the rest of the call is still recorded."""
        import soundcard as sc
        while not self._stop.is_set():
            try:
                spk = sc.default_speaker()
                # by endpoint id, not name: a Bluetooth headset's mic and speaker share one name
                dev = sc.get_microphone(id=spk.id, include_loopback=True)
                with dev.recorder(samplerate=RATE, channels=1, blocksize=BLOCK) as r:
                    if self.warning.startswith("שמע המחשב"):
                        self.warning = ""                 # recovered
                    checked = time.monotonic()
                    while not self._stop.is_set():
                        self._push(1, r.record(numframes=BLOCK)[:, 0].astype(np.float32))
                        if time.monotonic() - checked > 2.0:
                            checked = time.monotonic()
                            if sc.default_speaker().id != spk.id:
                                break                     # follow the new default output
            except Exception as e:
                if self._stop.is_set():
                    return
                self._loopback_failed(e)
                self._stop.wait(1.0)

    # ------------------------------------------------------------------ writer
    def _writer(self):
        ch = self.channels
        pending = [[] for _ in range(ch)]     # list of chunks per channel (no quadratic concatenation)
        avail = [0] * ch

        def take(i, n):
            out = np.empty(n, np.float32)
            pos = 0
            while pos < n:
                c = pending[i][0]
                k = min(len(c), n - pos)
                out[pos:pos + k] = c[:k]
                pos += k
                if k == len(c):
                    pending[i].pop(0)
                else:
                    pending[i][0] = c[k:]
            avail[i] -= n
            return out

        def pad(i, n):
            if n > 0:
                pending[i].append(np.zeros(n, np.float32))
                avail[i] += n

        def stalled(i, now):
            t = self.last_data[i]
            return t is None or now - t > STALL

        aligned = ch == 1
        try:
            while True:
                stopping = self._stop.is_set()
                for i, q in enumerate(self._queues):
                    while True:
                        try:
                            c = q.get_nowait()
                        except queue.Empty:
                            break
                        pending[i].append(c)
                        avail[i] += len(c)
                if ch == 2:
                    # Keep the channels aligned in time: a channel that starts late, pauses or fails is
                    # filled with silence for the time it was missing, instead of shifting the rest of the
                    # call (and without it, one dead channel would block writing the other).
                    now = time.monotonic()
                    if not aligned:
                        f0, f1 = self.first_data
                        if self.frames:
                            aligned = True              # writing already started (a channel was padded)
                        elif f0 is not None and f1 is not None:
                            k = int(abs(f1 - f0) * RATE)
                            late = 1 if f1 > f0 else 0
                            if k:
                                pending[late].insert(0, np.zeros(k, np.float32))
                                avail[late] += k
                            aligned = True
                    # give a late-starting channel up to 1 s to deliver its first block, so the start
                    # alignment above can place it exactly; after that, pad whatever is stalled
                    starting = not aligned and now - (self.started_at or now) < 1.0
                    for a, b in ((0, 1), (1, 0)):
                        gap = avail[a] - avail[b]
                        if not starting and (gap > MAX_SKEW * RATE or (gap > ALIGN_GAP * RATE and stalled(b, now))):
                            pad(b, gap)
                if stopping:
                    for i in range(ch):
                        pad(i, max(avail) - avail[i])
                n = min(avail)
                if n:
                    block = np.stack([take(i, n) for i in range(ch)], axis=1)
                    pcm = (np.clip(block, -1, 1) * 32767).astype("<i2")
                    self._wav.write(pcm.tobytes())
                    self.frames += n
                if stopping:
                    break
                time.sleep(0.05)
        except Exception as e:
            self.error = f"שמירת ההקלטה לדיסק נכשלה: {friendly_error(e)}"
            self._stop.set()
        finally:
            try:
                self._wav.close()
            except Exception:
                pass


def _unlink_retry(p: Path, attempts=10, delay=0.2):
    for i in range(attempts):
        try:
            p.unlink(missing_ok=True)
            return True
        except PermissionError:
            time.sleep(delay)
    return False


def wav_to_opus(wav_path: Path, out_ogg: Path, cancelled=lambda: False) -> float:
    """Encodes a recording WAV to Opus and deletes the WAV. Returns duration (s); 0.0 (and no ogg) if
    the WAV holds no audio. Also recovers a recording interrupted by a crash: the header's size fields
    may be stale or clamped, so the data size is taken from the file size."""
    from engine import encode_opus

    if not wav_has_audio(wav_path):
        _unlink_retry(wav_path)
        return 0.0
    with wave.open(str(wav_path), "rb") as w:
        ch, rate = w.getnchannels(), w.getframerate()
    frame_bytes = 2 * ch
    total = (wav_path.stat().st_size - WAV_HEADER) // frame_bytes
    if total <= 0:
        _unlink_retry(wav_path)
        return 0.0
    if not out_ogg.exists():
        def blocks():
            with open(wav_path, "rb") as f:
                f.seek(WAV_HEADER)
                while True:
                    raw = f.read(rate * 10 * frame_bytes)
                    raw = raw[: len(raw) - len(raw) % frame_bytes]
                    if not raw:
                        break
                    a = np.frombuffer(raw, "<i2").astype(np.float32).reshape(-1, ch) / 32768.0
                    yield a.T

        encode_opus(blocks(), out_ogg, channels=ch, rate=rate, cancelled=cancelled)
    # else: an earlier run already finished the (atomic) encode and only the delete was missed
    if not _unlink_retry(wav_path):
        print(f"could not delete {wav_path} (in use); it is removed on a later start", flush=True)
    return total / rate
