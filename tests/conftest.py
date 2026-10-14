"""Test harness for the recording pipeline of תמלול עברית — no audio hardware, no whisper model.

Run (from the project folder):
    .venv\\Scripts\\python.exe -m pytest tests -v
    .venv\\Scripts\\python.exe -m pytest tests -v -m "not desired"   # regression tests only

Tests marked `desired` assert behaviour the app SHOULD have but currently lacks; they are expected to
fail until the corresponding bug is fixed.

Safety: Qt runs offscreen, the library root is a per-test temp folder (HEBTRANSCRIBE_ROOT), the app's
QSettings are redirected to an INI file in the temp folder (the user's HKCU\\Software\\HebTranscribe is
never touched), message boxes are patched so nothing blocks, and `sounddevice` / `soundcard` are
replaced by fakes (tests/fakes.py) so no real device is ever opened.
"""

import sys
sys.dont_write_bytecode = True   # never write .pyc next to the app sources (only tests/ may change)

import faulthandler
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# must happen before anything creates a QApplication / reads the library root
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HEBTRANSCRIBE_ROOT"] = tempfile.mkdtemp(prefix="hebtranscribe_test_guard_")

import fakes  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "desired: asserts desired behaviour that is currently missing (expected to FAIL now)")


# --------------------------------------------------------------------------- per-test watchdog

@pytest.fixture(autouse=True)
def _watchdog():
    faulthandler.dump_traceback_later(120, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


# --------------------------------------------------------------------------- fake audio stack

@pytest.fixture
def world(monkeypatch):
    """A simulated machine (see fakes.World) installed as `sounddevice` + `soundcard`."""
    w = fakes.World()
    monkeypatch.setitem(sys.modules, "sounddevice", fakes.make_sounddevice(w))
    monkeypatch.setitem(sys.modules, "soundcard", fakes.make_soundcard(w))
    yield w
    for s in list(w.streams):
        s.close()


# --------------------------------------------------------------------------- helpers

def wait_until(cond, timeout=5.0, step=0.01):
    """Spins the Qt event loop (if any) until cond() is truthy. Returns the final cond() value."""
    from PySide6.QtCore import QCoreApplication
    end = time.monotonic() + timeout
    while True:
        v = cond()
        if v:
            return v
        if time.monotonic() > end:
            return cond()
        if QCoreApplication.instance() is not None:
            QCoreApplication.processEvents()
        time.sleep(step)


def read_wav(path):
    import numpy as np
    with wave.open(str(path), "rb") as w:
        ch, rate, n = w.getnchannels(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    a = np.frombuffer(raw, "<i2").astype(np.float32).reshape(-1, ch) / 32768.0
    return a, rate


def ogg_info(path):
    """(channels, sample_rate, decoded_seconds) of an Ogg/Opus file, by decoding it."""
    import av
    with av.open(str(path)) as c:
        st = c.streams.audio[0]
        ch = st.codec_context.channels
        n = 0
        rate = None
        for fr in c.decode(st):
            n += fr.samples
            rate = fr.sample_rate
    return ch, rate, n / rate if rate else 0.0


@pytest.fixture
def helpers():
    class H:
        pass
    h = H()
    h.wait_until = staticmethod(wait_until)
    h.read_wav = staticmethod(read_wav)
    h.ogg_info = staticmethod(ogg_info)
    return h


# --------------------------------------------------------------------------- Qt / MainWindow

@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    QCoreApplication.setOrganizationName("HebTranscribeTest")
    QCoreApplication.setApplicationName("HebTranscribeTest")
    app.setLayoutDirection(Qt.RightToLeft)
    yield app


class MsgBoxSpy:
    """Records every QMessageBox the app tries to show; answers questions with `self.answer`."""

    def __init__(self):
        from PySide6.QtWidgets import QMessageBox
        self.calls = []                       # (kind, title, text)
        self.answer = QMessageBox.StandardButton.Yes
        self.on_question = None               # optional hook(text) called before answering

    def kinds(self, *kinds):
        return [c for c in self.calls if c[0] in kinds]

    def texts(self):
        return "\n".join(str(c[2]) for c in self.calls)


@pytest.fixture
def msgbox(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    spy = MsgBoxSpy()

    def make(kind):
        def f(parent=None, title="", text="", *a, **k):
            spy.calls.append((kind, title, text))
            if kind == "question":
                if spy.on_question:
                    spy.on_question(text)
                return spy.answer
            return QMessageBox.StandardButton.Ok
        return staticmethod(f)

    for kind in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, kind, make(kind))

    def exec_(self, *a):
        spy.calls.append(("exec", self.windowTitle(), self.text()))
        return int(QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    return spy


def _stub_process(self, audio_path, source, num_speakers, on_progress, on_partial, cancelled):
    """Engine.process stand-in: opens/decodes the audio like the real loader (so broken files fail
    the same way) but skips whisper/diarization."""
    import av
    on_progress(0, "טוען שמע…")
    with av.open(str(audio_path)) as c:
        st = c.streams.audio[0]
        n, rate = 0, st.rate or 48000
        for fr in c.decode(st):
            n += fr.samples
            rate = fr.sample_rate
    dur = n / rate
    segs = [{"start": 0.0, "end": dur, "speaker": "0", "text": "בדיקה"}] if dur > 0.5 else []
    on_partial(segs)
    on_progress(1.0, "")
    return segs, ({"0": "דובר 1"} if segs else {}), dur


@pytest.fixture
def make_window(qapp, tmp_path, monkeypatch, world, msgbox):
    """Factory: make_window(mic=None) -> MainWindow on an isolated library + settings."""
    from PySide6.QtCore import QCoreApplication, QSettings
    import engine
    import hebtranscribe as ht

    root = tmp_path / "library"
    monkeypatch.setenv("HEBTRANSCRIBE_ROOT", str(root))
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(ht, "QSettings", lambda *a, **k: QSettings(ini, QSettings.IniFormat))
    monkeypatch.setattr(engine.Engine, "process", _stub_process)
    # run jobs in the Processor thread so the Engine.process stub applies (the app normally uses a
    # child process, see worker.py)
    monkeypatch.setenv("HEBTRANSCRIBE_INPROCESS", "1")
    windows = []

    def factory(mic=None, show=True):
        s = QSettings(ini, QSettings.IniFormat)
        s.setValue("mic", mic or "")
        s.sync()
        w = ht.MainWindow()
        assert Path(w.library.root) == root, "library root must be the temp folder"
        assert "HebTranscribe" not in w.settings.fileName() or w.settings.fileName().endswith("settings.ini")
        if show:
            w.show()
        w._test_root = root
        windows.append(w)
        return w

    yield factory

    for w in windows:
        try:
            if w.recorder:
                w.rec_timer.stop()
                try:
                    w.recorder.stop()
                except Exception:
                    pass
                w.recorder = None
            p = w.processor
            if p.isRunning():
                p.shutdown(15000)
            w.player.stop()
            w.hide()
            w.deleteLater()
        except RuntimeError:
            pass
    QCoreApplication.processEvents()


def user_was_warned(w, msgbox, baseline_hint=""):
    """True if the UI told the user something is wrong: a message box, a non-default visible hint on
    the recording screen, an error stored on the recorder, or a status-bar message."""
    from PySide6.QtWidgets import QStatusBar
    if msgbox.kinds("warning", "critical", "information", "exec"):
        return True
    hint = w.rec_hint
    if hint.isVisible() and hint.text().strip() and hint.text() != baseline_hint:
        return True
    if w.recorder is not None and getattr(w.recorder, "error", ""):
        return True
    sb = w.findChild(QStatusBar)
    if sb is not None and sb.currentMessage():
        return True
    return False
