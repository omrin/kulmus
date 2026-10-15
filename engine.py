"""Transcription (ivrit.ai whisper via faster-whisper) + speaker diarization (pyannote 3.1 pipeline, diarize.py)."""

import os
import threading
from pathlib import Path

import av
import numpy as np

MODEL_ID = "ivrit-ai/whisper-large-v3-turbo-ct2"
MODEL_BYTES = 1_620_000_000      # approximate download size, for the first-run progress bar
MODELS_DIR = Path(__file__).resolve().parent / "models"
SEG_MODEL = MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMB_MODEL = MODELS_DIR / "wespeaker.onnx"
SR = 16_000
THREADS = max(1, (os.cpu_count() or 4) // 2)  # physical cores

ME = "me"


class Cancelled(Exception):
    pass


class NoAudio(Exception):
    """The file has no audio track (e.g. a muted screen recording). Retrying cannot help."""

    def __init__(self):
        super().__init__("בקובץ אין ערוץ שמע")


def _replace_retry(src: Path, dst: Path, attempts=10, delay=0.2):
    """os.replace, retried while another program (player, antivirus, sync client) briefly holds dst."""
    import time
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


# --------------------------------------------------------------------------- audio io

def encode_opus(src_frames, out_path: Path, channels: int, rate: int = 48_000, bitrate: int | None = None,
                cancelled=lambda: False):
    """Encodes float32 frames (iterable of arrays shaped [channels, n]) to Ogg/Opus.

    Writes to "<name>.part" and renames it only after a clean finish, so an interrupted encode (crash,
    kill, cancel) never leaves a truncated audio file that looks complete."""
    layout = "stereo" if channels == 2 else "mono"
    part = out_path.with_name(out_path.name + ".part")
    c = av.open(str(part), "w", format="ogg")
    try:
        s = c.add_stream("libopus", rate=rate, layout=layout)
        # lower bitrates blur voices enough that speaker diarization merges different speakers
        s.bit_rate = bitrate or (64_000 if channels == 2 else 48_000)
        for block in src_frames:
            if cancelled():
                raise Cancelled()
            fr = av.AudioFrame.from_ndarray(np.ascontiguousarray(block, dtype=np.float32), format="fltp", layout=layout)
            fr.rate = rate
            for p in s.encode(fr):
                c.mux(p)
        for p in s.encode(None):
            c.mux(p)
        c.close()
        _replace_retry(part, out_path)
    except BaseException:
        try:
            c.close()
        except Exception:
            pass
        part.unlink(missing_ok=True)
        raise


def convert_to_opus(src: str, out_path: Path, progress=None, cancelled=lambda: False) -> float:
    """Converts any audio/video file to mono 48k Opus. Returns duration in seconds."""
    inp = av.open(src, metadata_errors="ignore")
    stream = next((s for s in inp.streams if s.type == "audio"), None)
    if stream is None:
        inp.close()
        raise NoAudio()
    total = float(stream.duration * stream.time_base) if stream.duration else (inp.duration or 0) / 1e6
    res = av.AudioResampler(format="fltp", layout="mono", rate=48_000)
    done = [0]

    def frames():
        for frame in inp.decode(stream):
            if cancelled():
                raise Cancelled()
            for f in res.resample(frame):
                arr = f.to_ndarray()
                done[0] += arr.shape[1]
                if progress and total:
                    progress(min(1.0, done[0] / 48_000 / total))
                yield arr
        for f in res.resample(None):
            yield f.to_ndarray()

    try:
        encode_opus(frames(), out_path, channels=1, cancelled=cancelled)
    finally:
        inp.close()
    return done[0] / 48_000


def load_channels(path: Path) -> list[np.ndarray]:
    """Returns 16 kHz float mono arrays per channel (1 or 2)."""
    from faster_whisper import decode_audio
    with av.open(str(path)) as c:
        if not c.streams.audio:
            raise NoAudio()
        ch = c.streams.audio[0].channels
    if ch >= 2:
        return list(decode_audio(str(path), sampling_rate=SR, split_stereo=True))
    return [decode_audio(str(path), sampling_rate=SR)]


# --------------------------------------------------------------------------- models

class Engine:
    def __init__(self):
        self._whisper = None
        self._embedder = None
        self._segmenter = None
        self._lock = threading.Lock()
        self.profiles: dict = {}          # saved voices {name: {"v": [...], "sec": s}} for this job
        self.last_voiceprints: dict = {}  # {speaker key: {"v": [...], "sec": s}} of the last job

    def embedder(self):
        if self._embedder is None:
            from voices import Embedder
            self._embedder = Embedder(THREADS)
        return self._embedder

    def model_cached(self) -> bool:
        from huggingface_hub import snapshot_download
        try:
            snapshot_download(MODEL_ID, local_files_only=True)
            return True
        except Exception:
            return False

    def whisper(self, progress=None):
        """The transcription model; downloads it on first use (1.6 GB), reporting progress(p, text)."""
        with self._lock:
            if self._whisper is None:
                from faster_whisper import WhisperModel
                from huggingface_hub import snapshot_download
                try:
                    # offline first: a cached model must not depend on the network (proxies, outages)
                    path = snapshot_download(MODEL_ID, local_files_only=True)
                except Exception:
                    path = self._download(progress)
                if progress:
                    progress(1.0, "טוען את מודל התמלול…")
                self._whisper = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=THREADS)
            return self._whisper

    @staticmethod
    def _download(progress):
        from huggingface_hub import snapshot_download
        from huggingface_hub.constants import HF_HUB_CACHE
        cache = Path(HF_HUB_CACHE) / ("models--" + MODEL_ID.replace("/", "--"))
        done = threading.Event()

        def size():
            total = 0
            for root, _, files in os.walk(cache):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return total

        def watch():
            while not done.wait(0.5):
                n = size()
                progress(min(0.99, n / MODEL_BYTES),
                         f"מוריד את מודל התמלול (פעם אחת בלבד)… {n // 2**20} / {MODEL_BYTES // 2**20} MB")

        if progress:
            threading.Thread(target=watch, daemon=True).start()
        try:
            return snapshot_download(MODEL_ID)
        except Exception as e:
            raise RuntimeError("לא ניתן להוריד את מודל התמלול. בדקו את החיבור לאינטרנט ונסו שוב.") from e
        finally:
            done.set()

    def diarize(self, audio: np.ndarray, num_speakers: int, progress=None, cancelled=lambda: False):
        """Returns [(start, end, speaker_int)] sorted by start (pyannote 3.1 pipeline, see diarize.py)."""
        import diarize
        if self._segmenter is None:
            self._segmenter = diarize.Segmenter(SEG_MODEL, THREADS)
        try:
            return diarize.diarize(audio, self._segmenter, self.embedder().vp, num_speakers, progress, cancelled)
        except diarize.Cancelled:
            raise Cancelled()

    # ------------------------------------------------------------------ pipeline

    def process(self, audio_path: Path, source: str, num_speakers: int,
                on_progress, on_partial, cancelled) -> tuple[list[dict], dict, float]:
        """
        Full transcription. Returns (segments, speakers, duration).
        num_speakers: 0 = automatic, 1 = no diarization, N = fixed (for calls: number of remote participants).
        """
        on_progress(0, "טוען שמע…")
        chans = load_channels(audio_path)
        is_call = source == "call" and len(chans) == 2
        if is_call:
            mic, sys_ = chans
            n = min(len(mic), len(sys_))
            mix = np.clip(mic[:n] + sys_[:n], -1.0, 1.0)
        else:
            mix = chans[0] if len(chans) == 1 else (chans[0] + chans[1]) / 2
        duration = len(mix) / SR
        diar_share = 0.0 if (num_speakers == 1 and not is_call) else 0.25

        on_progress(0, "טוען מודל…")
        model = self.whisper(lambda p, text: on_progress(0, text))
        on_progress(0, "מתמלל…")
        segs, info = model.transcribe(
            mix, language="he", task="transcribe", beam_size=5, vad_filter=True,
            condition_on_previous_text=False, word_timestamps=True)
        words, partial = [], []
        for s in segs:
            if cancelled():
                raise Cancelled()
            words.extend((w.start, w.end, w.word) for w in (s.words or []))
            partial.append({"start": s.start, "end": s.end, "speaker": "0", "text": s.text.strip()})
            on_partial(partial)
            on_progress((1 - diar_share) * min(1.0, s.end / max(duration, 1e-3)), "מתמלל…")

        if not words:
            return [], {}, duration

        def diar_progress(p):
            on_progress((1 - diar_share) + diar_share * p, "מזהה דוברים…")

        import voices
        emb = self.embedder()
        vectors = {}                     # cluster label -> (voiceprint, seconds)
        if is_call:
            # remote speech comes only through the system channel; everything else is the local user
            turns = self.diarize(sys_, num_speakers, diar_progress, cancelled) if len(sys_) > SR else []
            vectors = voices.speaker_vectors(sys_, turns, emb)
            labels = [_overlap_speaker(a, b, turns) for a, b, _ in words]
            labels = [ME if k is None else str(k) for k in labels]
            vectors = {str(k): v for k, v in vectors.items()}
            me = voices.voice_of_spans(mic, _spans(words, labels, ME), emb)
            if me is not None:
                vectors[ME] = me
        elif num_speakers == 1:
            labels = ["0"] * len(words)
            v = voices.voice_of_spans(mix, _spans(words, labels, "0"), emb)
            if v is not None:
                vectors["0"] = v
        else:
            turns = self.diarize(mix, num_speakers, diar_progress, cancelled)
            vectors = voices.speaker_vectors(mix, turns, emb)
            labels = [_overlap_speaker(a, b, turns) for a, b, _ in words]
            labels = _fill_none(labels)
            vectors = {str(k): v for k, v in vectors.items()}

        labels = _smooth(words, labels)
        segments, speakers, mapping = _build_segments(words, labels, is_call)
        # voiceprints and recognised names, keyed like the segments' speakers
        names = voices.identify({k: v for k, v in vectors.items() if k in mapping}, self.profiles)
        self.last_voiceprints = {}
        for label, key in mapping.items():
            if label in vectors:
                v, sec = vectors[label]
                self.last_voiceprints[key] = {"v": [round(float(x), 5) for x in v], "sec": round(sec, 1),
                                              "m": voices.VOICE_MODEL}
            if label in names:
                speakers[key] = names[label]
        on_progress(1.0, "")
        return segments, speakers, duration


# --------------------------------------------------------------------------- merging helpers

def _overlap_speaker(a, b, turns):
    best, bo = None, 0.0
    for s, e, k in turns:
        if s > b:
            break
        o = min(b, e) - max(a, s)
        if o > bo:
            best, bo = k, o
    return best


def _fill_none(labels):
    out, last = [], None
    for k in labels:
        last = k if k is not None else last
        out.append(last)
    first = next((k for k in out if k is not None), 0)
    return [str(first if k is None else k) for k in out]


def _runs(labels):
    runs, i = [], 0
    while i < len(labels):
        j = i
        while j + 1 < len(labels) and labels[j + 1] == labels[i]:
            j += 1
        runs.append((i, j))
        i = j + 1
    return runs


def _smooth(words, labels, min_dur=0.8, min_words=3):
    """Reassigns very short speaker runs (usually boundary errors) to a neighbouring run."""
    labels = list(labels)
    for _ in range(3):
        changed = False
        runs = _runs(labels)
        for idx, (i, j) in enumerate(runs):
            dur = words[j][1] - words[i][0]
            if len(runs) > 1 and dur < min_dur and (j - i + 1) < min_words:
                prev_k = labels[runs[idx - 1][0]] if idx > 0 else None
                next_k = labels[runs[idx + 1][0]] if idx + 1 < len(runs) else None
                k = prev_k if prev_k is not None else next_k
                for t in range(i, j + 1):
                    labels[t] = k
                changed = True
        if not changed:
            break
    return labels


def _spans(words, labels, key, gap=0.6):
    """Merged (start, end) spans of the words labelled `key`."""
    out = []
    for (a, b, _), k in zip(words, labels):
        if k != key:
            continue
        if out and a - out[-1][1] <= gap:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _build_segments(words, labels, is_call):
    segments = []
    cur = None
    for (a, b, w), k in zip(words, labels):
        if cur and cur["speaker"] == k and a - cur["end"] < 3.0:
            cur["text"] += w
            cur["end"] = b
        else:
            cur = {"start": a, "end": b, "speaker": k, "text": w}
            segments.append(cur)
    for s in segments:
        s["text"] = s["text"].strip()
    segments = [s for s in segments if s["text"]]

    # renumber speakers by first appearance
    order = []
    for s in segments:
        if s["speaker"] not in order:
            order.append(s["speaker"])
    remote = [k for k in order if k != ME]
    mapping = {k: str(i + 1) for i, k in enumerate(remote)}
    if ME in order:
        mapping[ME] = ME
    for s in segments:
        s["speaker"] = mapping[s["speaker"]]
    speakers = {}
    if ME in order:
        speakers[ME] = "אני"
    for k in remote:
        n = mapping[k]
        if is_call:
            speakers[n] = "משתתף" if len(remote) == 1 else f"משתתף {n}"
        else:
            speakers[n] = f"דובר {n}"
    return segments, speakers, mapping
