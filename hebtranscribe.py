"""קולמוס (Kulmus) — offline Hebrew transcription for Windows, macOS and Linux."""

import html
import os
import queue
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QProgressBar, QPushButton, QSlider, QStackedWidget, QStyle,
    QStyledItemDelegate, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

import icons
import library as lib
import voices
from engine import Cancelled, ME
from recorder import RATE, Recorder, find_input, friendly_error, list_inputs
from worker import JobError, make_backend

APP_NAME = "קולמוס"
APP_VERSION = "1.0.1"
REPO_URL = "https://github.com/omrin/kulmus"
AUDIO_EXT = "*.wav *.mp3 *.m4a *.ogg *.opus *.flac *.aac *.wma *.webm *.mp4 *.mkv *.mov *.amr *.3gp"

# ------------------------------------------------------------------ look & feel
C_BG = "#FFFFFF"
C_SIDE = "#F6F6F8"
C_LINE = "#E6E6EA"
C_TEXT = "#1D1D22"
C_MUTED = "#74747E"
C_ACCENT = "#4F46E5"
C_ACCENT_SOFT = "#ECEBFD"
C_RED = "#E5484D"
SPEAKER_COLORS = ["#4F46E5", "#0F9D8A", "#D9480F", "#C2255C", "#1C7ED6", "#7048E8", "#2B8A3E", "#B08800"]
ME_COLOR = "#495057"


QSS = f"""
* {{ font-family: 'Segoe UI'; color: {C_TEXT}; }}
QMainWindow, #main {{ background: {C_BG}; }}
#side {{ background: {C_SIDE}; border-left: 1px solid {C_LINE}; }}
#appTitle {{ font-size: 15px; font-weight: 600; }}
QLineEdit#search {{ background: {C_BG}; border: 1px solid {C_LINE}; border-radius: 8px; padding: 6px 10px; }}
QLineEdit#search:focus {{ border-color: {C_ACCENT}; }}
QListWidget#list {{ background: transparent; border: none; outline: none; }}
QPushButton.primary {{ background: {C_ACCENT}; color: white; border: none; border-radius: 9px; padding: 9px 10px; font-weight: 600; }}
QPushButton.primary:hover {{ background: #4338CA; }}
QPushButton.primary:disabled {{ background: #B9B6F3; }}
QPushButton.secondary {{ background: {C_BG}; border: 1px solid {C_LINE}; border-radius: 9px; padding: 9px 10px; }}
QPushButton.secondary:hover {{ border-color: #C9C9D1; background: #FAFAFB; }}
QPushButton.secondary:disabled {{ color: #B0B0B8; }}
QPushButton.danger {{ background: {C_RED}; color: white; border: none; border-radius: 20px; min-height: 22px; padding: 9px 30px; font-size: 15px; font-weight: 600; }}
QPushButton.danger:hover {{ background: #CE3C41; }}
QPushButton.link {{ background: transparent; border: none; color: {C_MUTED}; padding: 6px; }}
QPushButton.link:hover {{ color: {C_TEXT}; }}
QToolButton.icon {{ border: none; border-radius: 8px; padding: 7px; background: transparent; }}
QToolButton.icon:hover {{ background: #EFEFF3; }}
QToolButton.icon::menu-indicator {{ image: none; width: 0; }}
QLineEdit#title {{ font-size: 21px; font-weight: 600; border: none; border-bottom: 1px solid transparent; padding: 2px 0; background: transparent; }}
QLineEdit#title:hover {{ border-bottom-color: {C_LINE}; }}
QLineEdit#title:focus {{ border-bottom-color: {C_ACCENT}; }}
#meta, #hint {{ color: {C_MUTED}; }}
#card {{ background: {C_SIDE}; border-radius: 12px; }}
QToolButton#play {{ background: {C_ACCENT}; border: none; border-radius: 18px; }}
QToolButton#play:hover {{ background: #4338CA; }}
QToolButton#speed {{ border: none; color: {C_MUTED}; padding: 4px 6px; border-radius: 6px; }}
QToolButton#speed:hover {{ background: #E9E9EE; }}
QSlider::groove:horizontal {{ height: 4px; background: #DCDCE2; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {C_ACCENT}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ background: #DCDCE2; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {C_ACCENT}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
QProgressBar {{ background: #E6E6EC; border: none; border-radius: 2px; max-height: 4px; }}
QProgressBar::chunk {{ background: {C_ACCENT}; border-radius: 2px; }}
QTextBrowser#transcript {{ border: none; background: transparent; }}
QPushButton.chip {{ border: 1px solid {C_LINE}; border-radius: 12px; min-height: 18px; padding: 2px 12px; background: {C_BG}; }}
QPushButton.chip:hover {{ border-color: #C9C9D1; }}
#bigTime {{ font-size: 54px; font-weight: 300; }}
QDialog {{ background: {C_BG}; }}
QListWidget#voices {{ background: {C_BG}; border: 1px solid {C_LINE}; border-radius: 10px; padding: 4px; outline: none; }}
QListWidget#voices::item {{ padding: 9px 8px; border-radius: 6px; }}
QListWidget#voices::item:selected {{ background: {C_ACCENT_SOFT}; color: {C_TEXT}; }}
QMenu {{ background: white; border: 1px solid {C_LINE}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {C_ACCENT_SOFT}; color: {C_TEXT}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #D5D5DC; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def fmt_time(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


HEB_MONTHS = ["ינו׳", "פבר׳", "מרץ", "אפר׳", "מאי", "יוני", "יולי", "אוג׳", "ספט׳", "אוק׳", "נוב׳", "דצמ׳"]


def fmt_date(ts: float, with_time=True) -> str:
    d = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    if d.date() == today:
        day = "היום"
    elif (today - d.date()).days == 1:
        day = "אתמול"
    else:
        day = f"{d.day} ב{HEB_MONTHS[d.month - 1]}" + ("" if d.year == today.year else f" {d.year}")
    return f"{day}, {d:%H:%M}" if with_time else day


def fmt_size(n: int) -> str:
    return f"{n / 2**20:.1f}MB" if n >= 2**20 else f"{max(1, n // 1024)}KB"


def speaker_color(key: str) -> str:
    if key == ME:
        return ME_COLOR
    try:
        return SPEAKER_COLORS[(int(key) - 1) % len(SPEAKER_COLORS)]
    except ValueError:
        return SPEAKER_COLORS[0]


def open_path(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def os_description() -> str:
    import platform
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    if sys.platform == "win32":
        return f"Windows {platform.release()} (build {platform.version()})"
    try:
        return platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
    except OSError:
        return "Linux"


def report_issue():
    """Opens the bug-report form on GitHub with the version and OS already filled in."""
    from urllib.parse import urlencode
    query = urlencode({"template": "bug.yml", "version": APP_VERSION, "os": os_description()})
    QDesktopServices.openUrl(QUrl(f"{REPO_URL}/issues/new?{query}"))


def icon_button(name: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setIcon(icons.icon(name, C_TEXT))
    b.setIconSize(QSize(18, 18))
    b.setToolTip(tip)
    b.setProperty("class", "icon")
    b.setCursor(Qt.PointingHandCursor)
    return b


def button(text: str, cls: str) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("class", cls)
    b.setCursor(Qt.PointingHandCursor)
    return b


# ------------------------------------------------------------------ background processing

class Processor(QThread):
    """Single orchestrating thread: one item at a time. The heavy work itself runs in a child process
    (see worker.py), so it can neither freeze the UI nor starve a recording, and cancelling is instant.

    While the processor owns an item its status stays PROCESSING — including the final audio-save
    stage — and the final status is only published at the very end, so the UI never treats an item as
    idle (plays it, deletes its audio, re-queues it) while the worker still writes its files."""
    progress = Signal(str, float, str)       # item id, 0..1, stage
    partial = Signal(str, object)            # item id, segments
    finished_item = Signal(str)
    engine_status = Signal(float, str)       # warm-up / model download: 0..1 (-1 = error), text ("" = ready)

    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.jobs: "queue.Queue[lib.Item | None]" = queue.Queue()
        self.current: lib.Item | None = None
        self._queued: set[str] = set()
        self._requeue: dict[str, bool] = {}      # requests for the current item, run after it
        self._cancel_id = None
        self.warming = False                     # loading the model at startup

    def enqueue(self, item: lib.Item, finalize_only: bool = False):
        """finalize_only: only (re)create audio.ogg from recording.wav / the imported file."""
        if item is self.current:
            # e.g. "retry" pressed while the cancelled item is still saving its audio: run it afterwards
            self._requeue[item.id] = self._requeue.get(item.id, True) and finalize_only
            return
        if item.id in self._queued:
            if not finalize_only:
                item.data.pop("finalize_only", None)        # a full job supersedes an audio-only one
                item.save()
            return
        if finalize_only:
            item.data.pop("copy_cancelled", None)
            if not item.data.get("finalize_only"):
                item.data["prev_status"] = item.status if item.status in (lib.DONE, lib.ERROR) else lib.DONE
            item.data["finalize_only"] = True
        else:
            item.data.pop("finalize_only", None)
            item.data.pop("prev_status", None)
            item.data.update(error="", permanent=False)
        item.data.update(status=lib.QUEUED, progress=0, stage="ממתין בתור…")
        item.save()
        self._queued.add(item.id)
        self.jobs.put(item)

    def unqueue(self, item: lib.Item):
        """Cancels a waiting item (it is skipped when reached) and restores a sensible status."""
        self._queued.discard(item.id)
        self._requeue.pop(item.id, None)
        if item.data.pop("finalize_only", False):
            item.data["status"] = item.data.pop("prev_status", lib.DONE)
        elif item.segments:          # a queued re-transcription: keep the existing transcript
            item.data["status"] = lib.DONE
        else:
            item.data.update(status=lib.ERROR, error="התמלול בוטל")
        item.data.update(stage="", progress=0)

    def is_busy(self) -> bool:
        return self.current is not None or bool(self._queued)

    def waiting_ahead(self, item: lib.Item) -> int:
        """How many items will be processed before this waiting one (including the current one)."""
        with self.jobs.mutex:
            order = [i for i in self.jobs.queue if i is not None and i.id in self._queued]
        ahead = next((n for n, i in enumerate(order) if i is item), len(order))
        return ahead + (1 if self.current is not None and self.current is not item else 0)

    def owns(self, item: lib.Item) -> bool:
        return item is self.current or item.id in self._queued

    def cancel(self, item_id: str):
        self._cancel_id = item_id
        self._requeue.pop(item_id, None)

    def shutdown(self, timeout_ms: int = 20000):
        """Stops after the current step; waiting items stay 'queued' on disk and resume next start."""
        self.requestInterruption()
        try:
            while True:
                self.jobs.get_nowait()
        except queue.Empty:
            pass
        self._queued.clear()
        self._requeue.clear()
        self.jobs.put(None)
        ok = self.wait(timeout_ms)
        self.backend.shutdown()
        return ok

    def run(self):
        if getattr(self.backend, "warmup", True) and not os.environ.get("HEBTRANSCRIBE_INPROCESS"):
            # start the worker and load the model now, so the first transcription doesn't wait for it
            t = time.monotonic()
            self.warming = True
            try:
                self.backend.call("warmup", {}, lambda p, text: self.engine_status.emit(p, text),
                                  lambda *a: None, self.isInterruptionRequested)
                print(f"worker ready in {time.monotonic() - t:.1f}s", flush=True)
                self.engine_status.emit(1.0, "")
            except Cancelled:
                pass
            except Exception as e:
                print(f"worker warm-up failed: {e}", flush=True)
                self.engine_status.emit(-1.0, f"{e} התמלול יתחיל כשהמודל יהיה זמין.")
            finally:
                self.warming = False
        while True:
            item = self.jobs.get()
            if item is None or self.isInterruptionRequested():
                return
            if item.id not in self._queued:
                continue                      # cancelled while waiting
            self._queued.discard(item.id)
            if not item.folder.exists() or item.status != lib.QUEUED or item.data.get("deleted"):
                continue
            self.current = item
            self._cancel_id = None
            started = time.monotonic()
            try:
                self._process(item)
            except Exception:
                # never let one item kill the worker: every later item would wait forever
                traceback.print_exc()
                item.data.update(status=lib.ERROR, error="שגיאה פנימית בעיבוד", stage="")
                for k in ("finalize_only", "prev_status", "cancellable"):
                    item.data.pop(k, None)
                try:
                    if item.folder.exists():
                        item.save()
                except Exception:
                    traceback.print_exc()
            finally:
                print(f"{datetime.now():%H:%M:%S} job {item.id}: {item.status} in {time.monotonic() - started:.1f}s"
                      f" ({item.duration:.0f}s audio)", flush=True)
                self.current = None
                again = self._requeue.pop(item.id, None)
                if (again is not None and item.folder.exists() and not item.data.get("deleted")
                        and not self.isInterruptionRequested()):
                    try:
                        self.enqueue(item, finalize_only=again)
                    except Exception:
                        traceback.print_exc()
                self.finished_item.emit(item.id)

    def _process(self, item: lib.Item):
        user_cancelled = lambda: self._cancel_id == item.id or bool(item.data.get("deleted"))
        cancelled = lambda: user_cancelled() or self.isInterruptionRequested()
        last = [0.0]

        def prog(p, stage):
            item.data["progress"], item.data["stage"] = p, stage
            now = time.monotonic()
            if now - last[0] > 0.25 or p >= 1:
                last[0] = now
                self.progress.emit(item.id, p, stage)

        def partial(segs):
            item.data["partial"] = [dict(s) for s in segs]
            self.partial.emit(item.id, item.data["partial"])

        def call(kind, args, cancel=cancelled):
            return self.backend.call(kind, args, on_progress=prog, on_partial=partial, cancelled=cancel)

        finalize_only = bool(item.data.get("finalize_only"))
        prev_status = item.data.get("prev_status", lib.DONE)
        item.data["status"] = lib.PROCESSING
        wav = item.folder / lib.WAV_NAME
        src = item.data.get("pending_source")
        had_transcript = bool(item.data.get("segments"))
        final: dict = {}                 # status / error / warning, published when the item is released
        skip_copy = False

        if finalize_only:
            final = {"status": prev_status}
        else:
            item.data["warning"] = ""
            # Transcribe from the best source: the lossless WAV or the original imported file. The
            # compressed library copy is made afterwards.
            if item.wav_path:
                source_path = wav
            elif src and Path(src).exists() and not item.audio_path:
                source_path = Path(src)
            else:
                source_path = item.audio_path
            try:
                if not source_path:
                    raise JobError("קובץ השמע חסר")
                prog(0, "טוען…")
                res = call("process", {
                    "path": str(source_path), "source": item.source,
                    "num_speakers": item.data.get("num_speakers", 0),
                    "profiles": voices.load_profiles()})
                segs, speakers, duration = res[:3]
                voiceprints = res[3] if len(res) > 3 else {}
                # recognised voices already carry their names; names the user typed for this item fill in
                # the remaining default labels (unless that name is already on another speaker)
                old_names = item.data.get("custom_names", {})
                for k, v in list(speakers.items()):
                    n = old_names.get(k)
                    if n and voices.DEFAULT_NAME.match(v) and n not in speakers.values():
                        speakers[k] = n
                item.data.update(segments=segs, speakers=speakers, voiceprints=voiceprints,
                                 duration=duration or item.duration, progress=1)
                item.data.pop("partial", None)
                final = {"status": lib.DONE, "error": "", "permanent": False}
                if item.folder.exists() and not item.data.get("deleted") and self._can_finalize(item):
                    item.data.update(finalize_only=True, prev_status=lib.DONE)
                    item.save()
                self.partial.emit(item.id, [])          # page shows the finished transcript
            except Cancelled:
                item.data.pop("partial", None)
                if self.isInterruptionRequested() and not user_cancelled():
                    # app is closing: resume on the next start, from the lossless WAV if there is one
                    item.data.update(status=lib.QUEUED, stage="", progress=0)
                    if item.folder.exists() and not item.data.get("deleted"):
                        item.save()
                    return
                if had_transcript:      # a cancelled re-transcription keeps the previous transcript
                    final = {"status": lib.DONE, "warning": "התמלול מחדש בוטל — נשמר התמלול הקודם."}
                else:
                    final = {"status": lib.ERROR, "error": "התמלול בוטל"}
            except Exception as e:
                traceback.print_exc()
                msg = str(e) or e.__class__.__name__
                permanent = bool(getattr(e, "permanent", False))
                if had_transcript:
                    final = {"status": lib.DONE, "warning": f"התמלול מחדש נכשל: {msg} — נשמר התמלול הקודם."}
                else:
                    final = {"status": lib.ERROR, "error": msg, "permanent": permanent}
                skip_copy = permanent
            item.data.pop("partial", None)

        # Library copy of the audio. A recording's WAV is kept until it is encoded; only an app shutdown
        # interrupts this, and resume_pending redoes it on the next start.
        copy_warning = ""
        if item.folder.exists() and not item.data.get("deleted") and not skip_copy:
            try:
                if wav.exists():
                    if item.wav_path:
                        item.data["cancellable"] = False
                        prog(1, "שומר את ההקלטה…")
                        try:
                            d = call("wav_to_opus", {"wav": str(wav), "out": str(item.folder / lib.AUDIO_NAME)},
                                     cancel=self.isInterruptionRequested)
                        finally:
                            item.data.pop("cancellable", None)
                        item.data["duration"] = d or item.duration
                    else:
                        wav.unlink(missing_ok=True)      # header only: nothing was recorded
                elif src and not item.audio_path:
                    if not Path(src).exists():
                        copy_warning = "קובץ המקור לא נמצא, ולכן לא נשמר עותק של השמע בספרייה."
                    elif user_cancelled():
                        copy_warning = "עותק השמע לא נשמר בספרייה (בוטל)."
                        item.data["copy_cancelled"] = True
                    else:
                        prog(1, "שומר עותק של הקובץ…")
                        d = call("convert", {"src": src, "out": str(item.folder / lib.AUDIO_NAME)})
                        item.data["duration"] = d or item.duration
                        item.data.pop("pending_source", None)
            except Cancelled:
                if user_cancelled():
                    copy_warning = "עותק השמע לא נשמר בספרייה (בוטל)."
                    item.data["copy_cancelled"] = True
                # app shutdown: resume_pending redoes the copy on the next start
            except Exception as e:
                traceback.print_exc()
                copy_warning = f"שמירת קובץ השמע נכשלה: {e}"
        if copy_warning and final.get("status") == lib.ERROR and user_cancelled():
            copy_warning = ""        # "התמלול בוטל" already says it; retry redoes everything
        if copy_warning:
            if final.get("status") == lib.ERROR:
                final["error"] = f"{final.get('error', '')} · {copy_warning}".strip(" ·")
            else:
                final["warning"] = copy_warning
        elif finalize_only:
            final["warning"] = ""                       # an earlier save failure is resolved
        item.data.update(final)
        item.data["stage"] = ""
        for k in ("finalize_only", "prev_status"):
            item.data.pop(k, None)
        if item.folder.exists() and not item.data.get("deleted"):
            item.save()

    @staticmethod
    def _can_finalize(item: lib.Item) -> bool:
        """An audio-save step is pending: a recording WAV to encode (or a leftover one to remove), or an
        imported file whose library copy was not made (and the user didn't cancel it)."""
        src = item.data.get("pending_source")
        return bool((item.folder / lib.WAV_NAME).exists()
                    or (src and not item.audio_path and Path(src).exists() and not item.data.get("copy_cancelled")))


# ------------------------------------------------------------------ widgets

class ItemDelegate(QStyledItemDelegate):
    def __init__(self, window):
        super().__init__()
        self.w = window

    def sizeHint(self, option, index):
        return QSize(200, 62)

    def paint(self, p: QPainter, option, index):
        item: lib.Item = self.w.items.get(index.data(Qt.UserRole))
        if not item:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(6, 2, -6, -2)
        if option.state & QStyle.State_Selected:
            p.setBrush(QColor("#E8E7FB"))
        elif option.state & QStyle.State_MouseOver:
            p.setBrush(QColor("#EEEEF2"))
        else:
            p.setBrush(Qt.NoBrush)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, 8, 8)

        inner = r.adjusted(12, 9, -12, -9)
        # source glyph on the leading (right) side
        kind = {"mic": "mic", "call": "phone", "file": "file"}.get(item.source, "mic")
        p.drawPixmap(QRect(inner.right() - 17, inner.top() + 1, 16, 16), self.w.row_icons[kind])

        text_rect = QRect(inner.left(), inner.top(), inner.width() - 26, inner.height())
        tf = QFont("Segoe UI", 10)
        tf.setWeight(QFont.DemiBold)
        p.setFont(tf)
        p.setPen(QColor(C_TEXT))
        title = p.fontMetrics().elidedText(item.title, Qt.ElideLeft, text_rect.width())
        p.drawText(text_rect, Qt.AlignRight | Qt.AlignTop, title)

        sf = QFont("Segoe UI", 9)
        p.setFont(sf)
        if item.status in (lib.QUEUED, lib.PROCESSING):
            p.setPen(QColor(C_ACCENT))
            pct = int(item.data.get("progress", 0) * 100)
            sub = item.data.get("stage") or "ממתין…"
            if item.status == lib.QUEUED and self.w.processor.current is not item:
                n = self.w.processor.waiting_ahead(item)
                sub = f"ממתין בתור ({n} לפניו)" if n else "ממתין בתור…"
            if item.status == lib.PROCESSING and pct:
                sub = f"{sub.rstrip('…')} {pct}%"
        elif item.status == lib.ERROR:
            p.setPen(QColor(C_RED))
            sub = "שגיאה · " + (item.data.get("error") or "")
        else:
            p.setPen(QColor(C_MUTED))
            parts = [fmt_date(item.created), fmt_time(item.duration)]
            n = len(item.speakers)
            if n > 1:
                parts.append(f"{n} דוברים")
            if not item.playable_path:
                parts.append("ללא שמע")
            if item.data.get("warning"):
                parts.insert(0, "⚠")
            sub = " · ".join(parts)
        sub = p.fontMetrics().elidedText(sub, Qt.ElideLeft, text_rect.width())
        p.drawText(text_rect, Qt.AlignRight | Qt.AlignBottom, sub)
        p.restore()


class LevelMeter(QWidget):
    def __init__(self, label: str):
        super().__init__()
        self.label, self.value = label, 0.0
        self.setFixedHeight(26)
        self.setMinimumWidth(260)

    def set_value(self, v: float):
        self.value = v
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        label_w = 70
        p.setPen(QColor(C_MUTED))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(QRect(w - label_w, 0, label_w, h), Qt.AlignRight | Qt.AlignVCenter, self.label)
        bar = QRect(0, h // 2 - 3, w - label_w - 10, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#E6E6EC"))
        p.drawRoundedRect(bar, 3, 3)
        v = min(1.0, self.value * 1.6) ** 0.6
        fill = QRect(bar.right() - int(bar.width() * v), bar.top(), int(bar.width() * v), bar.height())
        p.setBrush(QColor(C_ACCENT))
        p.drawRoundedRect(fill, 3, 3)


class SpeakersDialog(QDialog):
    def __init__(self, parent, is_call: bool, current: int):
        super().__init__(parent)
        self.setWindowTitle("תמלול מחדש")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("כמה משתתפים יש בשיחה (מלבדך)?" if is_call else "כמה דוברים יש בהקלטה?"))
        self.combo = QComboBox()
        self.combo.addItem("זיהוי אוטומטי", 0)
        self.combo.addItem("משתתף אחד" if is_call else "דובר אחד (ללא הפרדה)", 1)
        for n in range(2, 9):
            self.combo.addItem(str(n), n)
        self.combo.setCurrentIndex(max(0, self.combo.findData(current)))
        lay.addWidget(self.combo)
        hint = QLabel("ציון מספר מדויק משפר את הפרדת הדוברים.")
        hint.setObjectName("hint")
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("תמלול")
        bb.button(QDialogButtonBox.Cancel).setText("ביטול")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class VoicesDialog(QDialog):
    """Saved voices (learned when the user names a speaker). Stored only on this computer."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("קולות מוכרים")
        self.resize(400, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        hint = QLabel("כשנותנים שם לדובר, האפליקציה לומדת את הקול שלו ומזהה אותו בהקלטות הבאות. "
                      "טביעות הקול נשמרות רק במחשב הזה.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.list = QListWidget()
        self.list.setObjectName("voices")
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.btn_del = button("מחיקת הקול", "secondary")
        self.btn_del.clicked.connect(self.delete_selected)
        close = button("סגירה", "primary")
        close.clicked.connect(self.accept)
        row.addWidget(self.btn_del)
        row.addStretch()
        row.addWidget(close)
        lay.addLayout(row)
        self.reload()

    def reload(self):
        self.list.clear()
        profiles = voices.load_profiles()
        for name, p in sorted(profiles.items()):
            li = QListWidgetItem(f"{name}   ·   {fmt_time(float(p.get('sec', 0)))} דיבור")
            li.setData(Qt.UserRole, name)
            self.list.addItem(li)
        if not profiles:
            li = QListWidgetItem("עוד אין קולות שמורים. לחצו על שם של דובר בתמלול כדי לתת לו שם.")
            li.setFlags(Qt.NoItemFlags)
            self.list.addItem(li)
        self.btn_del.setEnabled(bool(profiles))

    def delete_selected(self):
        li = self.list.currentItem()
        name = li.data(Qt.UserRole) if li else None
        if not name:
            return
        if QMessageBox.question(self, APP_NAME, f"למחוק את הקול השמור של „{name}”?\n"
                                "השם יישאר בהקלטות קיימות, אבל לא יזוהה בהקלטות חדשות.") != QMessageBox.Yes:
            return
        profiles = voices.load_profiles()
        profiles.pop(name, None)
        voices.save_profiles(profiles)
        self.reload()


# ------------------------------------------------------------------ main window

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 720)
        self.setMinimumSize(760, 480)
        self.setAcceptDrops(True)

        self.settings = QSettings("Kulmus", "app")
        if not self.settings.allKeys():
            # first start under the new name: carry the settings over from "HebTranscribe"
            old = QSettings("HebTranscribe", "app")
            for k in old.allKeys():
                self.settings.setValue(k, old.value(k))
            self.settings.sync()
        root, self._root_warning = self.resolve_library_root()
        self.library = lib.Library(root)
        self.items: dict[str, lib.Item] = {}
        self.current: lib.Item | None = None
        self.recorder: Recorder | None = None
        self.rec_item: lib.Item | None = None

        self.rec_wall_start = 0.0
        self.rec_peak = 0.0
        self._last_tick = 0.0
        self._watch_from = 0.0
        self._rec_notice = ""

        # single in-memory copy of every item, shared with the processor thread
        self.items = {i.id: i for i in self.library.load_all()}

        log_path = str(lib.app_dir() / "app.log")
        self.processor = Processor(make_backend(log_path))
        self.processor.progress.connect(self.on_progress)
        self.processor.partial.connect(self.on_partial)
        self.processor.finished_item.connect(self.on_item_finished)
        self.processor.engine_status.connect(self.on_engine_status)
        self.processor.start()

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, d))
        self.player.playbackStateChanged.connect(self.on_play_state)

        self.row_icons = {k: icons.pixmap(k, C_MUTED, 16) for k in ("mic", "phone", "file")}
        self.build_ui()
        self.reload_list()
        self.resume_pending()
        self.list.setFocus()

        self.rec_timer = QTimer(self)
        self.rec_timer.setInterval(80)
        self.rec_timer.timeout.connect(self.on_rec_tick)
        if self._root_warning:
            QTimer.singleShot(0, lambda: QMessageBox.warning(self, APP_NAME, self._root_warning))

    # ------------------------------------------------------------ UI construction
    def build_ui(self):
        root = QWidget()
        root.setObjectName("main")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self.build_sidebar())
        self.stack = QStackedWidget()
        self.stack.addWidget(self.build_empty())
        self.stack.addWidget(self.build_recording())
        self.stack.addWidget(self.build_item_page())
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)

    def build_sidebar(self):
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(310)
        v = QVBoxLayout(side)
        v.setContentsMargins(14, 16, 14, 14)
        v.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel(APP_NAME)
        t.setObjectName("appTitle")
        head.addWidget(t)
        head.addStretch()
        self.btn_settings = icon_button("settings", "הגדרות")
        self.settings_menu = QMenu(self)
        self.settings_menu.aboutToShow.connect(self.fill_settings_menu)
        self.btn_settings.setMenu(self.settings_menu)
        self.btn_settings.setPopupMode(QToolButton.InstantPopup)
        head.addWidget(self.btn_settings)
        v.addLayout(head)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("חיפוש בהקלטות ובטקסט")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.reload_list)
        v.addWidget(self.search)

        self.list = QListWidget()
        self.list.setObjectName("list")
        self.list.setItemDelegate(ItemDelegate(self))
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.currentItemChanged.connect(self.on_select)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.list_menu)
        v.addWidget(self.list, 1)

        self.engine_box = QWidget()
        eb = QVBoxLayout(self.engine_box)
        eb.setContentsMargins(2, 0, 2, 4)
        eb.setSpacing(4)
        self.engine_text = QLabel()
        self.engine_text.setObjectName("hint")
        self.engine_text.setWordWrap(True)
        self.engine_bar = QProgressBar()
        self.engine_bar.setTextVisible(False)
        self.engine_bar.setRange(0, 1000)
        eb.addWidget(self.engine_text)
        eb.addWidget(self.engine_bar)
        self.engine_box.hide()
        v.addWidget(self.engine_box)

        self.btn_rec = button("הקלטה", "primary")
        self.btn_call = button("הקלטת שיחה", "secondary")
        self.btn_import = button("ייבוא קובץ…", "link")
        self.btn_rec.setToolTip("הקלטה מהמיקרופון")
        self.btn_call.setToolTip("מקליט את המיקרופון ואת שמע המחשב יחד — ל-Zoom, Teams, Meet וכל אפליקציית שיחות")
        if sys.platform == "darwin":
            self.btn_call.setEnabled(False)
            self.btn_call.setToolTip("הקלטת שמע המחשב אינה זמינה ב-macOS בלי תוכנה נוספת. אפשר להקליט מהמיקרופון.")
        self.btn_rec.clicked.connect(lambda: self.start_recording("mic"))
        self.btn_call.clicked.connect(lambda: self.start_recording("call"))
        self.btn_import.clicked.connect(self.import_files)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.btn_rec)
        row.addWidget(self.btn_call)
        v.addLayout(row)
        self.btn_mic = button("", "link")
        self.btn_mic.setToolTip("בחירת מיקרופון")
        self.mic_menu = QMenu(self)
        self.mic_menu.aboutToShow.connect(self.fill_mic_menu)
        self.btn_mic.setMenu(self.mic_menu)
        self.btn_mic.setStyleSheet("QPushButton::menu-indicator { image: none; width: 0; }")
        self.update_mic_button()
        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_import)
        bottom.addStretch()
        bottom.addWidget(self.btn_mic)
        v.addLayout(bottom)
        return side

    # ------------------------------------------------------------ library folder
    def resolve_library_root(self):
        """(root, warning). The saved folder, unless it is unavailable (e.g. a disconnected drive)."""
        if os.environ.get("HEBTRANSCRIBE_ROOT"):
            return lib.default_root(), ""
        default = lib.default_root()
        saved = self.settings.value("library_root")
        if not isinstance(saved, str) or not saved:
            return default, ""
        problem = lib.root_problem(Path(saved))
        if problem is None:
            return Path(saved), ""
        return default, (f"תיקיית ההקלטות שנבחרה („{saved}”) אינה זמינה כרגע: {problem}.\n\n"
                         "ההקלטות שבה לא מוצגות, ונפתחה תיקיית ברירת המחדל. חברו את הכונן ופתחו מחדש את "
                         "האפליקציה, או בחרו תיקייה אחרת דרך ⚙ ← שינוי תיקייה.")

    def fill_settings_menu(self):
        m = self.settings_menu
        m.clear()
        root = str(self.library.root)
        label = self.fontMetrics().elidedText(root, Qt.ElideMiddle, 320)
        info = m.addAction(f"תיקיית ההקלטות: {label}")
        info.setEnabled(False)
        info.setToolTip(root)
        m.addAction("שינוי תיקייה…", self.choose_library_folder)
        n = len(voices.load_profiles())
        m.addAction(f"קולות מוכרים ({n})…", self.manage_voices)
        m.addAction("פתיחת התיקייה", lambda: open_path(self.library.root))
        default = lib.default_root()
        if Path(root) != default:
            m.addAction("חזרה לתיקיית ברירת המחדל", lambda: self.switch_library(default))
        m.addSeparator()
        m.addAction("דיווח על באג או הצעה לשיפור…", report_issue)
        m.addAction("פתיחת תיקיית היומן", lambda: open_path(lib.app_dir()))
        about = m.addAction(f"{APP_NAME} {APP_VERSION}")
        about.setEnabled(False)

    def choose_library_folder(self):
        new = QFileDialog.getExistingDirectory(self, "בחירת תיקייה להקלטות ולתמלולים", str(self.library.root))
        if new:
            self.switch_library(Path(new))

    def switch_library(self, new: Path) -> bool:
        """Changes the library folder; optionally moves the existing recordings there."""
        if self.recorder is not None or self.processor.is_busy():
            QMessageBox.information(self, APP_NAME, "אי אפשר להחליף תיקייה בזמן הקלטה או תמלול. "
                                                    "נסו שוב כשהעבודה תסתיים.")
            return False
        old = self.library.root
        try:
            new_r, old_r = new.resolve(), old.resolve()
        except OSError:
            new_r, old_r = new, old
        if new_r == old_r:
            return False
        if new_r.is_relative_to(old_r) or old_r.is_relative_to(new_r):
            QMessageBox.warning(self, APP_NAME, "בחרו תיקייה שאינה נמצאת בתוך תיקיית ההקלטות הנוכחית "
                                                "ואינה מכילה אותה.")
            return False
        problem = lib.root_problem(new)
        if problem:
            QMessageBox.warning(self, APP_NAME, f"אי אפשר לשמור הקלטות בתיקייה „{new}”:\n{problem}")
            return False
        items = list(self.items.values())
        move = False
        if items:
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setIcon(QMessageBox.Question)
            box.setText(f"מה לעשות עם {len(items)} ההקלטות שבתיקייה הנוכחית?")
            box.setInformativeText(f"מ: {old}\nאל: {new}")
            b_move = box.addButton("להעביר לתיקייה החדשה", QMessageBox.AcceptRole)
            b_keep = box.addButton("להשאיר במקומן", QMessageBox.DestructiveRole)
            box.addButton("ביטול", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() not in (b_move, b_keep):
                return False
            move = box.clickedButton() is b_move
        # leave the item page first: nothing may (re)open an audio file of an item that is being moved
        # (e.g. a late 'item finished' signal re-showing it would lock its audio in the player)
        self.list.setCurrentItem(None)
        self.show_item(None)
        self.release_player()
        failed = []
        if move:
            from PySide6.QtWidgets import QProgressDialog
            dlg = QProgressDialog("מעביר הקלטות…", None, 0, len(items), self)
            dlg.setWindowTitle(APP_NAME)
            dlg.setMinimumDuration(300)
            for n, it in enumerate(items):
                dlg.setValue(n)
                QApplication.processEvents()
                try:
                    self.library.move_item(it, new, pump=QApplication.processEvents)
                except Exception as e:
                    traceback.print_exc()
                    failed.append(f"{it.title}: {e}")
            dlg.setValue(len(items))
        if new_r == lib.default_root().resolve():
            self.settings.remove("library_root")
        else:
            self.settings.setValue("library_root", str(new))
        self.settings.sync()
        self.load_library(new)
        if failed:
            QMessageBox.warning(self, APP_NAME, "חלק מההקלטות לא הועברו (הן נשארו בתיקייה הקודמת):\n\n"
                                + "\n".join(failed[:15]))
        return True

    def load_library(self, root: Path):
        self.release_player()
        self.current = None
        self.search.clear()
        self.library = lib.Library(root)
        self.items = {i.id: i for i in self.library.load_all()}
        self.reload_list()
        self.show_item(None)
        self.resume_pending()

    # ------------------------------------------------------------ microphone choice
    def mic_name(self) -> str | None:
        v = self.settings.value("mic")
        return v if isinstance(v, str) and v else None

    def update_mic_button(self):
        name = self.mic_name()
        try:
            missing = bool(name) and find_input(name) is None
        except Exception:
            missing = False
        fm = self.btn_mic.fontMetrics()
        if name:
            # elide only the device name, and isolate it so a Latin name keeps its order in RTL text
            label = "⁨" + fm.elidedText(name, Qt.ElideRight, 105) + "⁩"
        else:
            label = "ברירת מחדל"
        self.btn_mic.setText(f"מיקרופון: {label}{' (לא מחובר)' if missing else ''} ▾")
        self.btn_mic.setToolTip(
            f"מיקרופון: {name or 'ברירת המחדל של Windows'}"
            + ("\nהמיקרופון לא מחובר כרגע — תוקלט ברירת המחדל של Windows" if missing else ""))

    def fill_mic_menu(self):
        self.mic_menu.clear()
        current = self.mic_name()
        try:
            names = list_inputs(refresh=self.recorder is None)   # picks up devices plugged in since
        except Exception as e:
            names = []
            self.mic_menu.addAction(f"שגיאה: {e}").setEnabled(False)
        if current and current not in names:
            names.append(current)            # keep the saved choice visible even while unplugged
        for label, value in [("ברירת המחדל של Windows", None)] + [(n, n) for n in names]:
            act = self.mic_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(value == current or (value is None and not current))
            act.triggered.connect(lambda _=False, v=value: self.set_mic(v))
        self.update_mic_button()

    def set_mic(self, name):
        self.settings.setValue("mic", name or "")
        self.settings.sync()
        self.update_mic_button()

    def build_empty(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addStretch()
        g = QLabel()
        g.setPixmap(icons.pixmap("mic", "#C4C4CC", 46, 1.4))
        g.setObjectName("emptyGlyph")
        g.setAlignment(Qt.AlignCenter)
        v.addWidget(g)
        self.empty_title = QLabel("אין הקלטה נבחרת")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_title.setStyleSheet("font-size: 17px; font-weight: 600;")
        v.addWidget(self.empty_title)
        hint = QLabel("התחילו הקלטה, הקליטו שיחה, או גררו לכאן קובץ שמע או וידאו.")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignCenter)
        v.addWidget(hint)
        v.addStretch()
        return w

    def build_recording(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addStretch(2)
        self.rec_label = QLabel()
        self.rec_label.setAlignment(Qt.AlignCenter)
        self.rec_label.setStyleSheet(f"color: {C_RED}; font-weight: 600; font-size: 14px;")
        v.addWidget(self.rec_label)
        self.rec_time = QLabel("00:00")
        self.rec_time.setObjectName("bigTime")
        self.rec_time.setAlignment(Qt.AlignCenter)
        v.addWidget(self.rec_time)
        meters = QVBoxLayout()
        meters.setSpacing(4)
        self.meter_mic = LevelMeter("מיקרופון")
        self.meter_sys = LevelMeter("שמע המחשב")
        for m in (self.meter_mic, self.meter_sys):
            meters.addWidget(m, 0, Qt.AlignHCenter)
        v.addLayout(meters)
        self.rec_device = QLabel()
        self.rec_device.setObjectName("hint")
        self.rec_device.setAlignment(Qt.AlignCenter)
        v.addWidget(self.rec_device)
        self.rec_hint = QLabel()
        self.rec_hint.setTextFormat(Qt.RichText)      # set_rec_hint() builds escaped HTML
        self.rec_hint.setObjectName("hint")
        self.rec_hint.setAlignment(Qt.AlignCenter)
        self.rec_hint.setWordWrap(True)
        v.addSpacing(10)
        v.addWidget(self.rec_hint)
        v.addSpacing(18)
        self.btn_stop = button("עצירה ותמלול", "danger")
        self.btn_stop.clicked.connect(self.stop_recording)
        v.addWidget(self.btn_stop, 0, Qt.AlignHCenter)
        self.btn_discard = button("ביטול ההקלטה", "link")
        self.btn_discard.clicked.connect(self.discard_recording)
        v.addWidget(self.btn_discard, 0, Qt.AlignHCenter)
        v.addStretch(3)
        return w

    def build_item_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(32, 22, 32, 16)
        v.setSpacing(10)

        head = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("title")
        self.title_edit.editingFinished.connect(self.rename_item)
        head.addWidget(self.title_edit, 1)
        self.btn_copy = icon_button("copy", "העתקת התמלול")
        self.btn_copy.clicked.connect(self.copy_text)
        self.btn_export = icon_button("export", "ייצוא")
        em = QMenu(self)
        em.addAction("קובץ טקסט (txt)", lambda: self.export("txt"))
        em.addAction("כתוביות (srt)", lambda: self.export("srt"))
        self.btn_export.setMenu(em)
        self.btn_export.setPopupMode(QToolButton.InstantPopup)
        self.btn_more = icon_button("more", "עוד")
        self.more_menu = QMenu(self)
        self.act_redo = self.more_menu.addAction("תמלול מחדש…", self.retranscribe)
        self.act_folder = self.more_menu.addAction("פתיחת התיקייה", self.open_folder)
        self.more_menu.addSeparator()
        self.act_del_audio = self.more_menu.addAction("מחיקת קובץ השמע (שמירת התמלול)", self.delete_audio_current)
        self.act_delete = self.more_menu.addAction("מחיקת ההקלטה", self.delete_current)
        self.btn_more.setMenu(self.more_menu)
        self.btn_more.setPopupMode(QToolButton.InstantPopup)
        for b in (self.btn_copy, self.btn_export, self.btn_more):
            head.addWidget(b)
        v.addLayout(head)

        self.meta = QLabel()
        self.meta.setObjectName("meta")
        v.addWidget(self.meta)

        # player card
        self.card = QFrame()
        self.card.setObjectName("card")
        ph = QHBoxLayout(self.card)
        ph.setContentsMargins(12, 8, 14, 8)
        ph.setSpacing(12)
        self.btn_play = QToolButton()
        self.btn_play.setObjectName("play")
        self.btn_play.setIcon(icons.icon("play", "#FFFFFF", 16))
        self.btn_play.setIconSize(QSize(16, 16))
        self.btn_play.setFixedSize(36, 36)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_play.clicked.connect(self.toggle_play)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setLayoutDirection(Qt.LeftToRight)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.pos_label = QLabel("00:00")
        self.pos_label.setObjectName("meta")
        self.speed_btn = QToolButton()
        self.speed_btn.setObjectName("speed")
        self.speed_btn.setText("1×")
        self.speed_btn.setLayoutDirection(Qt.LeftToRight)
        self.speed_btn.setToolTip("מהירות ניגון")
        self.speed_btn.clicked.connect(self.cycle_speed)
        ph.addWidget(self.btn_play)
        ph.addWidget(self.pos_label)
        ph.addWidget(self.slider, 1)
        ph.addWidget(self.speed_btn)
        v.addWidget(self.card)
        self.no_audio = QLabel()
        self.no_audio.setObjectName("hint")
        v.addWidget(self.no_audio)

        # processing banner
        self.banner = QWidget()
        bl = QVBoxLayout(self.banner)
        bl.setContentsMargins(0, 4, 0, 4)
        bl.setSpacing(6)
        brow = QHBoxLayout()
        self.banner_text = QLabel()
        self.banner_text.setObjectName("meta")
        self.banner_btn = button("ביטול", "link")
        self.banner_btn.clicked.connect(self.banner_action)
        brow.addWidget(self.banner_text, 1)
        brow.addWidget(self.banner_btn)
        bl.addLayout(brow)
        self.banner_bar = QProgressBar()
        self.banner_bar.setTextVisible(False)
        self.banner_bar.setRange(0, 1000)
        bl.addWidget(self.banner_bar)
        v.addWidget(self.banner)

        # speakers
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        chips_w = QWidget()
        chips_w.setLayout(self.chips)
        self.chips.setContentsMargins(0, 0, 0, 0)
        self.chips_w = chips_w
        v.addWidget(chips_w)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {C_LINE};")
        v.addWidget(line)

        self.transcript = QTextBrowser()
        self.transcript.setObjectName("transcript")
        self.transcript.setOpenLinks(False)
        self.transcript.anchorClicked.connect(self.on_anchor)
        self.transcript.document().setDocumentMargin(4)
        self.transcript.setFont(QFont("Segoe UI", 12))
        v.addWidget(self.transcript, 1)
        return w

    # ------------------------------------------------------------ list
    def reload_list(self):
        sel = self.current.id if self.current else None
        q = self.search.text().strip()
        self.list.blockSignals(True)
        self.list.clear()
        for item in sorted(self.items.values(), key=lambda i: i.created, reverse=True):
            if self.rec_item and item.id == self.rec_item.id:
                continue
            if q and q not in item.title and q not in item.plain_text():
                continue
            li = QListWidgetItem()
            li.setData(Qt.UserRole, item.id)
            self.list.addItem(li)
            if item.id == sel:
                self.list.setCurrentItem(li)
        self.list.blockSignals(False)
        if not self.items:
            self.empty_title.setText("עוד אין הקלטות")
        else:
            self.empty_title.setText("אין הקלטה נבחרת")

    def find_row(self, item_id):
        for r in range(self.list.count()):
            if self.list.item(r).data(Qt.UserRole) == item_id:
                return self.list.item(r)
        return None

    def refresh_row(self, item_id):
        li = self.find_row(item_id)
        if li:
            self.list.update(self.list.indexFromItem(li))

    def on_select(self, cur, prev):
        if self.recorder:
            return
        item = self.items.get(cur.data(Qt.UserRole)) if cur else None
        self.show_item(item)

    def select_item(self, item_id):
        li = self.find_row(item_id)
        if li is None and self.search.text():
            self.search.clear()                 # the new item didn't match the filter
            li = self.find_row(item_id)
        if li:
            self.list.setCurrentItem(li)
        else:
            self.list.setCurrentItem(None)
            self.show_item(self.items.get(item_id))

    def list_menu(self, pos: QPoint):
        ids = [li.data(Qt.UserRole) for li in self.list.selectedItems()]
        if not ids:
            return
        items = [self.items[i] for i in ids if i in self.items]
        with_audio = [i for i in items if (i.audio_path or i.wav_path) and not self.busy(i)]
        m = QMenu(self)
        if with_audio:
            size = sum(i.audio_size() for i in with_audio)
            m.addAction(f"מחיקת קבצי השמע ({len(with_audio)}, {fmt_size(size)})",
                        lambda: self.delete_audio(with_audio))
        m.addAction(f"מחיקה ({len(items)})" if len(items) > 1 else "מחיקה", lambda: self.delete_items(items))
        m.exec(self.list.mapToGlobal(pos))

    # ------------------------------------------------------------ item page
    def release_player(self):
        """Stops playback and lets go of the audio file (Windows locks open files)."""
        self.player.stop()
        self.player.setSource(QUrl())

    def busy(self, item: lib.Item) -> bool:
        return item.status in (lib.QUEUED, lib.PROCESSING) or self.processor.owns(item)

    def show_item(self, item: lib.Item | None):
        if self.recorder is not None:
            # Never leave the recording screen while recording: it holds the only Stop button.
            # The page is refreshed when the recording ends.
            self.current = item
            return
        same = item is self.current
        if not same:
            self.release_player()
        self.current = item
        if item is None:
            self.stack.setCurrentIndex(0)
            return
        self.stack.setCurrentIndex(2)
        if not (same and (self.title_edit.hasFocus() or self.title_edit.isModified())):
            self.title_edit.setText(item.title)     # don't overwrite a title being typed
            self.title_edit.setModified(False)
        busy = self.busy(item)
        audio = item.playable_path
        if busy:
            self.release_player()     # the player is hidden while processing; don't leave audio playing
        elif audio and self.player.source().isEmpty():
            self.player.setSource(QUrl.fromLocalFile(str(audio)))
        self.card.setVisible(bool(audio) and not busy)
        self.no_audio.setVisible(not audio and item.status == lib.DONE)
        self.no_audio.setText("קובץ השמע נמחק כדי לחסוך מקום — התמלול נשמר."
                              if item.data.get("audio_deleted") else "אין קובץ שמע להקלטה זו.")
        self.update_meta()
        self.update_banner()
        self.render_chips()
        self.render_transcript(keep_scroll=same)

    def update_meta(self):
        item = self.current
        parts = [fmt_date(item.created), fmt_time(item.duration)]
        n = len(item.speakers)
        if n > 1:
            parts.append(f"{n} דוברים")
        if item.source == "call":
            parts.append("שיחה")
        if item.data.get("original_name"):
            parts.append(item.data["original_name"])
        if item.audio_path or item.wav_path:
            parts.append(f"שמע {fmt_size(item.audio_size())}")
        self.meta.setText("  ·  ".join(parts))
        busy = self.busy(item)
        self.act_redo.setEnabled(item.has_source_audio() and not busy)
        self.act_del_audio.setEnabled(bool(item.audio_path or item.wav_path) and not busy)
        self.act_delete.setEnabled(True)
        final_text = bool(item.segments) and (not busy or (self.processor.current is item
                                                             and not item.data.get("partial")
                                                             and item.data.get("stage", "").startswith("שומר")))
        self.btn_copy.setEnabled(final_text)
        self.btn_export.setEnabled(final_text)

    def _can_finalize(self, item) -> bool:
        return Processor._can_finalize(item)

    def _manual_copy_possible(self, item) -> bool:
        src = item.data.get("pending_source")
        return bool((item.folder / lib.WAV_NAME).exists() or (src and not item.audio_path and Path(src).exists()))

    def update_banner(self):
        item = self.current
        self.banner_btn.setVisible(True)
        if item.status in (lib.QUEUED, lib.PROCESSING):
            self.banner.show()
            self.banner_bar.show()
            pct = item.data.get("progress", 0)
            self.banner_bar.setValue(int(pct * 1000))
            stage = item.data.get("stage") or "ממתין בתור…"
            if item.status == lib.QUEUED and self.processor.current is not item:
                stage = self.queue_text(item)
            if item.data.get("cancellable") is False:
                self.banner_bar.setRange(0, 0)            # indeterminate: the encode reports no progress
                self.banner_text.setText(html.escape(stage))
                self.banner_btn.setVisible(False)
            else:
                self.banner_bar.setRange(0, 1000)
                self.banner_text.setText(html.escape(stage) + (f"  {int(pct * 100)}%" if pct else ""))
                self.banner_btn.setText("ביטול")
        elif item.status == lib.ERROR:
            self.banner.show()
            self.banner_bar.hide()
            self.banner_text.setText(f"<span style='color:{C_RED}'>{html.escape(item.data.get('error', ''))}</span>")
            self.banner_btn.setText("ניסיון חוזר")
            self.banner_btn.setVisible(item.has_source_audio() and not item.data.get("permanent"))
        elif item.data.get("warning"):
            self.banner.show()
            self.banner_bar.hide()
            self.banner_text.setText(f"<span style='color:#B45309'>{html.escape(item.data['warning'])}</span>")
            self.banner_btn.setText("ניסיון חוזר לשמירת השמע" if self._manual_copy_possible(item) else "הסתרה")
        else:
            self.banner.hide()

    def queue_text(self, item) -> str:
        n = self.processor.waiting_ahead(item)
        cur = self.processor.current
        if not n:
            return "מכין את מנוע התמלול…" if self.processor.warming else "ממתין בתור…"
        text = "ממתין בתור — לפניו עוד " + ("פריט אחד" if n == 1 else f"{n} פריטים")
        if cur is not None and cur is not item:
            text += f" (מעבד כעת: „{cur.title}”)"
        return text

    def banner_action(self):
        item = self.current
        if item is None:
            return
        if item.status in (lib.QUEUED, lib.PROCESSING):
            if self.processor.current is item:
                self.processor.cancel(item.id)
                self.banner_text.setText("מבטל…")
                return
            self.processor.unqueue(item)
            item.save()
        elif item.status == lib.ERROR:
            self.release_player()
            self.processor.enqueue(item)
        elif item.data.get("warning"):
            if self._manual_copy_possible(item):
                self.release_player()
                self.processor.enqueue(item, finalize_only=True)
            else:
                item.data["warning"] = ""
                item.save()
        self.show_item(item)
        self.refresh_row(item.id)

    def render_chips(self):
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        item = self.current
        if not item.speakers or item.status != lib.DONE or not item.segments:
            self.chips_w.hide()
            return
        self.chips_w.show()
        talk = {}
        for s in item.segments:
            talk[s["speaker"]] = talk.get(s["speaker"], 0) + s["end"] - s["start"]
        total = sum(talk.values()) or 1
        for key, name in item.speakers.items():
            b = button("", "chip")
            b.setText(f"●  {name}  {int(100 * talk.get(key, 0) / total)}%")
            b.setStyleSheet(f"color: {speaker_color(key)};")
            b.setToolTip("לחיצה לשינוי השם — האפליקציה תלמד את הקול ותזהה אותו אוטומטית בהקלטות הבאות")
            b.clicked.connect(lambda _=False, k=key: self.rename_speaker(k))
            self.chips.addWidget(b)
        self.chips.addStretch()

    def render_transcript(self, keep_scroll=False):
        item = self.current
        sb = self.transcript.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        old = sb.value()
        busy = self.busy(item)
        # while (re)transcribing, show the live partial result; the saved transcript stays untouched
        segments = item.data.get("partial") or [] if busy else item.segments
        if busy and not segments:
            segments = item.segments
        multi = self.show_speakers(item) and item.status == lib.DONE
        out = ["<div dir='rtl'>"]
        if not segments:
            msg = {lib.QUEUED: "ממתין לתמלול…", lib.PROCESSING: "התמלול יופיע כאן תוך כדי עיבוד…"}.get(item.status, "")
            if item.status == lib.DONE:
                msg = "לא זוהה דיבור בהקלטה."
            out.append(f"<p style='color:{C_MUTED}'>{msg}</p>")
        for s in segments:
            text = html.escape(s["text"])
            t = fmt_time(s["start"])
            link = f"<a href='t:{s['start']}' style='color:{C_TEXT}; text-decoration:none'>{text}</a>"
            if multi:
                key = s["speaker"]
                name = html.escape(item.speakers.get(key, key))
                out.append(
                    f"<p style='margin:0 0 2px 0'>"
                    f"<a href='s:{key}' style='color:{speaker_color(key)}; font-weight:600; text-decoration:none'>{name}</a>"
                    f"&nbsp;&nbsp;<a href='t:{s['start']}' style='color:{C_MUTED}; font-size:10pt; text-decoration:none'>{t}</a></p>"
                    f"<p style='margin:0 0 16px 0; line-height:145%'>{link}</p>")
            else:
                out.append(
                    f"<p style='margin:0 0 10px 0; line-height:150%'><a href='t:{s['start']}' style='color:{C_MUTED}; font-size:10pt; "
                    f"text-decoration:none'>{t}</a>&nbsp;&nbsp;{link}</p>")
        out.append("</div>")
        self.transcript.setHtml("".join(out))
        if keep_scroll:
            sb.setValue(sb.maximum() if at_bottom else old)

    def on_anchor(self, url: QUrl):
        s = url.toString()
        if s.startswith("t:"):
            if self.current and self.card.isVisible() and not self.player.source().isEmpty():
                self.player.setPosition(int(float(s[2:]) * 1000))
                self.player.play()
        elif s.startswith("s:"):
            self.rename_speaker(s[2:])

    @staticmethod
    def show_speakers(item) -> bool:
        """Label the transcript by speaker: several speakers, or one who has a real name."""
        names = list(item.speakers.values())
        return len(names) > 1 or any(not voices.DEFAULT_NAME.match(n) and n != "אני" for n in names)

    def rename_speaker(self, key):
        item = self.current
        old = item.speakers.get(key, "")
        name, ok = QInputDialog.getText(
            self, "שם הדובר", "שם (האפליקציה תזהה את הקול הזה גם בהקלטות הבאות):", text=old)
        name = name.strip()
        if not ok or not name or name == old:
            return
        for k, v in item.speakers.items():          # the same name on two speakers = they are one person
            if k != key and v == name:
                item.speakers[k] = old or v
        item.speakers[key] = name
        item.data.setdefault("custom_names", {})[key] = name
        item.save()
        self.render_chips()
        self.render_transcript(keep_scroll=True)
        self.refresh_row(item.id)
        vp = (item.data.get("voiceprints") or {}).get(key)
        if vp and not voices.DEFAULT_NAME.match(name):
            profiles = voices.load_profiles()
            voices.learn(profiles, name, vp["v"], float(vp.get("sec", 0)) or 5.0)
            try:
                voices.save_profiles(profiles)
            except OSError:
                traceback.print_exc()
                return
            n = self.apply_voice_to_library(name, profiles[name], skip=item)
            if n:
                QMessageBox.information(self, APP_NAME, f"הקול של „{name}” נשמר, וזוהה גם ב-"
                                        + ("הקלטה נוספת אחת." if n == 1 else f"{n} הקלטות נוספות."))

    def apply_voice_to_library(self, name, profile, skip=None) -> int:
        """Puts a newly learned name on matching unnamed speakers in the other recordings."""
        changed = 0
        for it in self.items.values():
            if it is skip or self.busy(it) or name in it.speakers.values():
                continue
            best = None
            custom = it.data.get("custom_names", {})
            for key, vp in (it.data.get("voiceprints") or {}).items():
                cur = it.speakers.get(key, "")
                if key == ME or key in custom or not voices.DEFAULT_NAME.match(cur):
                    continue
                sim = voices.similarity(vp["v"], profile)
                if sim >= voices.ID_SIM and (best is None or sim > best[0]):
                    best = (sim, key)
            if best:
                it.speakers[best[1]] = name
                try:
                    it.save()
                except OSError:
                    traceback.print_exc()
                    continue
                changed += 1
                self.refresh_row(it.id)
        return changed

    def manage_voices(self):
        dlg = VoicesDialog(self)
        dlg.exec()

    def rename_item(self):
        item = self.current
        if item and self.title_edit.text().strip() and self.title_edit.text() != item.title:
            item.data["title"] = self.title_edit.text().strip()
            item.save()
            self.refresh_row(item.id)

    # ------------------------------------------------------------ player
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def on_play_state(self, st):
        self.btn_play.setIcon(icons.icon("pause" if st == QMediaPlayer.PlayingState else "play", "#FFFFFF", 16))

    def on_position(self, ms):
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.pos_label.setText(f"{fmt_time(ms / 1000)} / {fmt_time(self.player.duration() / 1000)}")

    def cycle_speed(self):
        speeds = [1.0, 1.25, 1.5, 2.0]
        cur = self.player.playbackRate()
        nxt = speeds[(speeds.index(cur) + 1) % len(speeds)] if cur in speeds else 1.0
        self.player.setPlaybackRate(nxt)
        self.speed_btn.setText(f"{nxt:g}×")

    # ------------------------------------------------------------ recording
    NO_AUDIO_SECONDS = 3.0      # no block from the microphone for this long -> the stream is dead

    def start_recording(self, mode):
        if self.recorder is not None:
            return
        self.player.pause()
        item = self.library.create(mode)
        item.data["status"] = lib.RECORDING
        item.save()
        saved_mic = self.mic_name()
        rec = Recorder(mode, item.folder / lib.WAV_NAME, mic_name=saved_mic)
        try:
            rec.start()
        except Exception as e:
            traceback.print_exc()
            rec.stop()
            try:
                self.library.delete(item)
            except Exception:
                traceback.print_exc()
            self.update_mic_button()
            QMessageBox.warning(
                self, APP_NAME,
                f"לא ניתן להתחיל הקלטה מהמיקרופון „{rec.device_name or saved_mic or 'ברירת המחדל'}”:\n"
                f"{friendly_error(e)}\n\nבדקו שהמיקרופון מחובר, או בחרו מיקרופון אחר בתחתית הרשימה.")
            return
        now = time.monotonic()
        self.recorder, self.rec_item = rec, item
        self.rec_wall_start = self._last_tick = now
        self._watch_from = now
        self.rec_peak = 0.0
        self.sys_peak = 0.0
        self._rec_notice = None          # forces the first set_rec_hint() to rewrite the label
        self.rec_label.setText("● מקליט שיחה" if mode == "call" else "● מקליט")
        self.rec_time.setText("00:00")
        self.meter_sys.setVisible(mode == "call")
        self.meter_mic.label = "אני" if mode == "call" else "מיקרופון"
        self.meter_sys.label = "משתתפים"
        self.meter_mic.set_value(0)
        self.meter_sys.set_value(0)
        dev = f"מיקרופון: ⁨{rec.device_name}⁩"
        if rec.mic_missing:
            dev += f"  (המיקרופון שנבחר, „{saved_mic}”, לא מחובר — מוקלטת ברירת המחדל)"
        self.rec_device.setText(dev)
        self._default_hint = (
            "מקליט את המיקרופון שלך ואת שמע המחשב (Zoom, Teams, Meet וכו'). "
            "מומלץ להשתמש באוזניות — כך ההפרדה בינך לבין המשתתפים מדויקת יותר."
            if mode == "call" else "")
        self.set_rec_hint()
        for b in (self.btn_rec, self.btn_call, self.btn_import, self.list, self.btn_mic):
            b.setEnabled(False)
        self.stack.setCurrentIndex(1)
        self.rec_timer.start()

    def set_rec_hint(self, silent=False, warning=""):
        parts = []
        if warning:
            parts.append(f"<span style='color:#B45309'>{html.escape(warning)}</span>")
        if silent:
            parts.append(f"<span style='color:{C_RED}'>לא נקלט קול מהמיקרופון.</span> בדקו שהוא לא מושתק, "
                         "או עצרו את ההקלטה, בחרו מיקרופון אחר בתחתית הרשימה והתחילו מחדש.")
        elif self._default_hint:
            parts.append(html.escape(self._default_hint))
        text = "<br>".join(parts)
        if text != self._rec_notice:
            self._rec_notice = text
            self.rec_hint.setText(text)
            self.rec_hint.setVisible(bool(text))

    def on_rec_tick(self):
        r = self.recorder
        if r is None:
            return
        now = time.monotonic()
        if now - self._last_tick > 1.0:
            # the UI thread itself was held up; don't judge the stream on stale timestamps
            self._watch_from = now + 1.0
        self._last_tick = now
        if r.error:
            self._recording_failed(r.error)
            return
        last = r.last_data[0] or r.started_at or self.rec_wall_start
        if now >= self._watch_from and (now - last > self.NO_AUDIO_SECONDS or not r.mic_alive()):
            self._recording_failed(None)
            return
        self.rec_time.setText(fmt_time(r.seconds))
        fresh = [r.last_data[i] is not None and now - r.last_data[i] < 0.6 for i in (0, 1)]
        self.meter_mic.set_value(r.levels[0] if fresh[0] else 0.0)
        self.meter_sys.set_value(r.levels[1] if fresh[1] else 0.0)
        if fresh[0]:
            self.rec_peak = max(self.rec_peak, r.levels[0])
        if fresh[1]:
            self.sys_peak = max(self.sys_peak, r.levels[1])
        elapsed = now - self.rec_wall_start
        silent = elapsed > 4 and self.rec_peak < 0.003
        warning = r.warning
        if not warning and r.mode == "call" and elapsed > 15 and self.sys_peak < 0.003:
            warning = ("עד כה לא נקלט שמע מהמחשב. ודאו שאפליקציית השיחה מנגנת לרמקול ברירת המחדל של Windows "
                       "(אם אף אחד עוד לא דיבר — אפשר להתעלם).")
        self.set_rec_hint(silent, warning)

    def _end_recording(self):
        """Stops the recorder and gives the UI back. Idempotent; never leaves the UI in recording state."""
        rec, item = self.recorder, self.rec_item
        if rec is None:
            return None, None
        self.rec_timer.stop()
        self.recorder = self.rec_item = None
        try:
            rec.stop()
        except Exception:
            traceback.print_exc()
        finally:
            for b in (self.btn_rec, self.btn_call, self.btn_import, self.list, self.btn_mic):
                b.setEnabled(True)
            self.btn_call.setEnabled(sys.platform != "darwin")
            self.update_mic_button()
        if rec.overflows:
            print(f"recording {item.id}: {rec.overflows} input overflow(s)", flush=True)
        return rec, item

    def _finish_recording(self, rec, item) -> bool:
        """Keeps (and queues for transcription) a recording with audio, deletes an empty one. A call whose
        microphone never delivered is empty too, even though the writer padded it with silence."""
        if rec.frames >= RATE // 2 and rec.last_data[0] is not None:
            item.data["duration"] = rec.seconds
            self.items[item.id] = item
            self.processor.enqueue(item)
            self.reload_list()
            self.select_item(item.id)
            return True
        try:
            self.library.delete(item)
        except Exception:
            traceback.print_exc()
        self.reload_list()
        self.show_item(self.current if self.current and self.current.id in self.items else None)
        return False

    def stop_recording(self):
        wall = time.monotonic() - self.rec_wall_start
        rec, item = self._end_recording()
        if rec is None:
            return
        if not self._finish_recording(rec, item):
            if (rec.frames == 0 or rec.last_data[0] is None) and wall >= 1.5:
                QMessageBox.warning(self, APP_NAME, self._no_audio_text(rec.device_name))
            else:
                QMessageBox.information(self, APP_NAME, "ההקלטה קצרה מדי ולא נשמרה.")

    def _no_audio_text(self, device):
        return (f"לא הגיע שמע מהמיקרופון „{device}”, ולכן ההקלטה לא נשמרה.\n\n"
                "בדקו שהמיקרופון מחובר ולא מושתק, או בחרו מיקרופון אחר בתחתית הרשימה („מיקרופון: …”) ונסו שוב.")

    def _recording_failed(self, error):
        """The recorder reported an error, or the microphone stopped delivering audio."""
        rec, item = self._end_recording()
        if rec is None:
            return
        kept = self._finish_recording(rec, item)
        if error:
            msg = f"ההקלטה נעצרה בגלל שגיאה:\n{error}"
        elif rec.frames == 0 or rec.last_data[0] is None:
            msg = self._no_audio_text(rec.device_name)
        else:
            msg = (f"המיקרופון „{rec.device_name}” הפסיק להעביר שמע (ייתכן שנותק), וההקלטה נעצרה.")
        if kept and rec.frames >= RATE // 2:
            msg += f"\n\nמה שהוקלט עד כה ({fmt_time(rec.seconds)}) נשמר ונשלח לתמלול."
        QMessageBox.warning(self, APP_NAME, msg)

    def discard_recording(self):
        rec_item = self.rec_item
        if QMessageBox.question(self, APP_NAME, "לבטל את ההקלטה? היא לא תישמר.") != QMessageBox.Yes:
            return
        if self.recorder is None:
            # the recording already ended while the question was open (e.g. a device error): honour the discard
            if rec_item is not None and rec_item.id in self.items:
                self._delete_now([rec_item])
            return
        rec, item = self._end_recording()
        try:
            self.library.delete(item)
        except Exception:
            traceback.print_exc()
        self.reload_list()
        self.show_item(self.current if self.current and self.current.id in self.items else None)

    # ------------------------------------------------------------ import
    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "בחירת קבצי שמע", "",
                                                f"שמע ווידאו ({AUDIO_EXT});;כל הקבצים (*)")
        self.add_files(paths)

    def add_files(self, paths):
        last = None
        for p in paths:
            if not p or not Path(p).is_file():
                continue
            item = self.library.create("file", title=Path(p).stem, original_name=Path(p).name)
            item.data["pending_source"] = p
            self.items[item.id] = item
            self.processor.enqueue(item)
            last = item
        if last:
            self.reload_list()
            self.select_item(last.id)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and not self.recorder:
            e.acceptProposedAction()

    def dropEvent(self, e):
        if self.recorder is None:
            self.add_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    # ------------------------------------------------------------ processing events
    def resume_pending(self):
        for item in sorted(self.items.values(), key=lambda i: i.created):
            st = item.status
            if st == lib.RECORDING:
                # the app was closed or crashed during this recording
                if item.wav_path:
                    self.processor.enqueue(item)
                else:
                    (item.folder / lib.WAV_NAME).unlink(missing_ok=True)
                    item.data.update(status=lib.ERROR, error="ההקלטה לא קלטה שמע (המיקרופון לא העביר נתונים).")
                    item.save()
            elif st in (lib.QUEUED, lib.PROCESSING):
                self.processor.enqueue(item, finalize_only=bool(item.data.get("finalize_only")))
            elif st == lib.DONE and self._can_finalize(item):
                self.processor.enqueue(item, finalize_only=True)    # audio copy was interrupted

    def on_engine_status(self, p, text):
        """First-run model download / warm-up, shown under the list (only while it matters)."""
        if not text:
            self.engine_box.hide()
            return
        self.engine_text.setText(text)
        if p < 0:
            self.engine_text.setStyleSheet(f"color: {C_RED};")
            self.engine_bar.hide()
        else:
            self.engine_text.setStyleSheet("")
            self.engine_bar.show()
            self.engine_bar.setValue(int(p * 1000))
        # a plain warm-up (model already on disk) takes seconds: only show the download
        self.engine_box.setVisible(p < 0 or "מוריד" in text)

    def on_progress(self, item_id, p, stage):
        self.refresh_row(item_id)
        if self.current and self.recorder is None:
            if self.current.id == item_id:
                self.update_banner()
                self.update_meta()
            elif self.current.status == lib.QUEUED:
                self.update_banner()          # the queue moved: "N ahead" / "processing now"
        self.list.viewport().update()

    def on_partial(self, item_id, segs):
        if self.current and self.current.id == item_id and self.recorder is None:
            self.render_transcript(keep_scroll=True)

    def on_item_finished(self, item_id):
        item = self.items.get(item_id)
        if item is None:
            # deleted while it was being processed: remove its files now that the worker let go of them
            folder = self.library.root / item_id
            if folder.exists():
                try:
                    self.library.delete(lib.Item(folder, {"id": item_id}))
                except Exception:
                    traceback.print_exc()
                    QMessageBox.warning(self, APP_NAME, "לא ניתן היה למחוק את קובץ השמע של הקלטה שנמחקה — "
                                        "ייתכן שהוא פתוח בתוכנה אחרת. הוא יימחק בפתיחה הבאה של האפליקציה.")
            return
        self.refresh_row(item_id)
        self.list.viewport().update()          # waiting rows show their new position
        if self.current is item and self.recorder is None:
            self.release_player()
            self.show_item(item)
        elif self.current is not None and self.current.status == lib.QUEUED and self.recorder is None:
            self.update_banner()

    def retranscribe(self):
        item = self.current
        if item is None or self.busy(item):
            return
        dlg = SpeakersDialog(self, item.source == "call", item.data.get("num_speakers", 0))
        if dlg.exec() == QDialog.Accepted and self.current is item:
            item.data["num_speakers"] = dlg.combo.currentData()
            self.release_player()
            self.processor.enqueue(item)
            self.show_item(item)
            self.refresh_row(item.id)

    # ------------------------------------------------------------ delete / export
    def delete_audio_current(self):
        if self.current:
            self.delete_audio([self.current])

    def delete_audio(self, items):
        items = [i for i in items if not self.busy(i)]
        if not items:
            return
        size = sum(i.audio_size() for i in items)
        what = "קובץ השמע" if len(items) == 1 else f"{len(items)} קבצי שמע"
        untranscribed = [i for i in items if not i.segments]
        note = ("התמלול יישמר, אבל לא יהיה אפשר להאזין או לתמלל מחדש."
                if not untranscribed else
                "שימו לב: " + ("להקלטה זו" if len(items) == 1 else f"ל-{len(untranscribed)} מהן") +
                " אין תמלול — זה העותק היחיד של השמע.")
        if QMessageBox.question(self, APP_NAME, f"למחוק את {what} ({fmt_size(size)})?\n{note}") != QMessageBox.Yes:
            return
        if self.current in items:
            self.release_player()
        failed = []
        for i in items:
            try:
                self.library.delete_audio(i)
            except Exception:
                failed.append(i.title)
                traceback.print_exc()
            self.refresh_row(i.id)
        if self.current is not None:
            self.show_item(self.current)
        if failed:
            QMessageBox.warning(self, APP_NAME, "לא ניתן היה למחוק את קובץ השמע של:\n" + "\n".join(failed) +
                                "\n\nייתכן שהקובץ פתוח בתוכנה אחרת. סגרו אותה ונסו שוב.")

    def delete_current(self):
        if self.current:
            self.delete_items([self.current])

    def delete_items(self, items):
        what = f"את \"{items[0].title}\"" if len(items) == 1 else f"{len(items)} הקלטות"
        if QMessageBox.question(self, APP_NAME, f"למחוק {what}, כולל התמלול?\nלא ניתן לבטל.") != QMessageBox.Yes:
            return
        self._delete_now(items)

    def _delete_now(self, items):
        failed = []
        for i in items:
            if self.current is i:
                self.release_player()
            if self.processor.current is i:
                # the worker lets go of the files first; the folder is removed in on_item_finished
                i.data["deleted"] = True
                try:
                    i.save()                  # persisted, so a crash/close still purges it on next start
                except Exception:
                    traceback.print_exc()
                self.processor.cancel(i.id)
            else:
                self.processor.unqueue(i)
                try:
                    self.library.delete(i)
                except Exception:
                    traceback.print_exc()
                    failed.append(i.title)
                    continue
            self.items.pop(i.id, None)
            if self.current is i:
                self.current = None
        self.reload_list()
        self.show_item(self.current)
        if failed:
            QMessageBox.warning(self, APP_NAME, "לא ניתן היה למחוק את:\n" + "\n".join(failed) +
                                "\n\nייתכן שקובץ השמע פתוח בתוכנה אחרת. סגרו אותה ונסו שוב.")

    def item_text(self, item, with_times=True) -> str:
        multi = self.show_speakers(item)
        lines = []
        for s in item.segments:
            prefix = f"[{fmt_time(s['start'])}] " if with_times else ""
            name = f"{item.speakers.get(s['speaker'], '')}: " if multi else ""
            lines.append(prefix + name + s["text"])
        return ("\n\n" if multi else "\n").join(lines)

    def copy_text(self):
        QApplication.clipboard().setText(self.item_text(self.current, with_times=False))
        self.btn_copy.setToolTip("הועתק ✓")
        QTimer.singleShot(1500, lambda: self.btn_copy.setToolTip("העתקת התמלול"))

    def export(self, kind):
        item = self.current
        safe = "".join(c for c in item.title if c not in '\\/:*?"<>|').strip() or "תמלול"
        path, _ = QFileDialog.getSaveFileName(self, "ייצוא", str(Path.home() / "Documents" / f"{safe}.{kind}"),
                                              f"*.{kind}")
        if not path:
            return
        if kind == "txt":
            text = f"{item.title}\n{fmt_date(item.created)} · {fmt_time(item.duration)}\n\n" + self.item_text(item)
        else:
            def ts(x):
                ms = int(round(x * 1000))
                return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
            multi = self.show_speakers(item)
            blocks = []
            for n, s in enumerate(item.segments, 1):
                name = f"{item.speakers.get(s['speaker'], '')}: " if multi else ""
                blocks.append(f"{n}\n{ts(s['start'])} --> {ts(s['end'])}\n{name}{s['text']}\n")
            text = "\n".join(blocks)
        try:
            Path(path).write_text(text, encoding="utf-8-sig")
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"השמירה נכשלה:\n{e}\n\nייתכן שהקובץ פתוח בתוכנה אחרת.")

    def open_folder(self):
        open_path(self.current.folder)

    # ------------------------------------------------------------ shutdown
    def closeEvent(self, e):
        if self.recorder is not None:
            if QMessageBox.question(self, APP_NAME, "יש הקלטה פעילה. לעצור ולשמור אותה?") != QMessageBox.Yes:
                e.ignore()
                return
            if self.recorder is not None:      # may have ended while the question was open
                self.stop_recording()
        if self.processor.is_busy() and QMessageBox.question(
                self, APP_NAME, "יש תמלולים בעיבוד. לצאת בכל זאת? הם ימשיכו בפתיחה הבאה.") != QMessageBox.Yes:
            e.ignore()
            return
        self.release_player()
        self.hide()
        self.processor.shutdown()
        e.accept()


def setup_console_fallback():
    # Under pythonw (the desktop shortcut) there is no console: sys.stdout/stderr are None and any
    # library that prints (tqdm progress bars, tracebacks) crashes. Send all output to a log file.
    if sys.stdout is None or sys.stderr is None:
        root = lib.app_dir()
        root.mkdir(parents=True, exist_ok=True)
        log = open(root / "app.log", "a", encoding="utf-8", buffering=1)
        log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
        import faulthandler
        faulthandler.enable(file=log)   # native crashes would otherwise vanish without a trace


def _server_name(root: Path, prefix: str = "kulmus-") -> str:
    import getpass
    import hashlib
    return prefix + hashlib.md5(f"{getpass.getuser()}|{root}".encode()).hexdigest()[:12]


def _show_running(name: str) -> bool:
    """Asks a running instance (listening on `name`) to come to the front. True if one answered."""
    from PySide6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(500):
        return False
    sock.write(b"show")
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def single_instance(root: Path):
    """Returns (lock, server) for the first instance, or None after handing over to a running one.
    Two instances would both resume and process the same items (and the live recording)."""
    from PySide6.QtCore import QLockFile
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    name = _server_name(root)
    lock = QLockFile(str(root / "app.lock"))
    lock.setStaleLockTime(0)              # stale only when the owning process is gone
    if not lock.tryLock(300):
        sock = QLocalSocket()
        sock.connectToServer(name)
        if sock.waitForConnected(1500):
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(1500)
            sock.disconnectFromServer()
            return None
        # nobody answers: the lock was left by a crash (its PID may have been reused); take it over
        if not (lock.removeStaleLockFile() and lock.tryLock(300)):
            QMessageBox.information(None, APP_NAME, "האפליקציה כבר פתוחה.")
            return None
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)
    return lock, server


def selftest(argv) -> int:
    """Headless check of a built app (used by the release builds):
        Kulmus --selftest REPORT.json [--warmup] [--audio FILE] [--record]
    Starts the real UI offscreen, optionally records 2 s from the microphone and transcribes FILE through
    the real worker process, and writes a JSON report. Exit code 0 = everything requested worked."""
    import json
    report_path = Path(argv[argv.index("--selftest") + 1])
    audio = argv[argv.index("--audio") + 1] if "--audio" in argv else None
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if not os.environ.get("HEBTRANSCRIBE_ROOT"):
        import tempfile
        os.environ["HEBTRANSCRIBE_ROOT"] = tempfile.mkdtemp(prefix="kulmus-selftest-")
    rep = {"platform": sys.platform, "frozen": bool(getattr(sys, "frozen", False)), "ok": True}
    app = QApplication(sys.argv[:1])
    try:
        w = MainWindow()
        rep["icons"] = not icons.pixmap("mic").isNull()
        try:
            rep["microphones"] = list_inputs()
        except Exception as e:
            rep["microphones"] = f"error: {e}"

        def pump(cond, timeout):
            end = time.monotonic() + timeout
            while time.monotonic() < end and not cond():
                app.processEvents()
                time.sleep(0.05)
            return cond()

        if "--warmup" in argv:
            # first run on a clean machine: download + load the model through the worker process
            status = {}
            w.processor.engine_status.connect(lambda p, text: status.update(p=p, text=text))
            pump(lambda: w.processor.warming or status, 10)          # warm-up has started
            pump(lambda: not w.processor.warming, 3600)               # ... and finished
            app.processEvents()
            ready = status.get("text", "") == "" and status.get("p", 1.0) >= 0
            rep["warmup"] = {"ready": bool(ready), "last": status.get("text", "")}
            rep["ok"] &= rep["warmup"]["ready"]
        if "--record" in argv:
            QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
            QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
            w.start_recording("mic")
            ok = w.recorder is not None and pump(lambda: w.recorder is not None and w.recorder.seconds >= 2.0, 8)
            rep["record_seconds"] = round(w.recorder.seconds, 2) if w.recorder else 0
            w.stop_recording()
            rep["record_ok"] = bool(ok)
            rep["ok"] &= bool(ok)
        if audio:
            t = time.monotonic()
            w.add_files([audio])
            it = max(w.items.values(), key=lambda i: i.created)
            done = pump(lambda: it.status in (lib.DONE, lib.ERROR) and not w.processor.is_busy(), 3600)
            rep["transcribe"] = {"status": it.status, "error": it.data.get("error", ""), "seconds": round(time.monotonic() - t),
                                 "speakers": it.speakers, "text": " ".join(s["text"] for s in it.segments)[:300]}
            rep["ok"] &= bool(done and it.status == lib.DONE and it.segments)
        w.processor.shutdown()
    except Exception:
        rep["ok"] = False
        rep["exception"] = traceback.format_exc()
    report_path.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if rep["ok"] else 1


def main():
    if "--selftest" in sys.argv:
        setup_console_fallback()
        return selftest(sys.argv)
    setup_console_fallback()
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("kulmus.app")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "icon.ico")))
    app.setLayoutDirection(Qt.RightToLeft)
    app.setFont(QFont("Segoe UI", 10))
    # an instance of the previous version (its lock lived in the library folder) is still running
    if _show_running(_server_name(lib.default_root(), "hebtranscribe-")) or \
            _show_running(_server_name(lib.app_dir().parent / "HebTranscribe", "hebtranscribe-")):
        return 0
    root = lib.app_dir()
    root.mkdir(parents=True, exist_ok=True)
    inst = single_instance(root)
    if inst is None:
        return 0
    old_lock = lib.default_root() / "app.lock"       # left over from the previous version
    try:
        old_lock.unlink(missing_ok=True)
    except OSError:
        pass
    lock, server = inst
    app.setStyleSheet(QSS)
    w = MainWindow()

    def bring_to_front():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.close()
        w.showNormal()
        w.raise_()
        w.activateWindow()

    server.newConnection.connect(bring_to_front)
    w.show()
    rc = app.exec()
    lock.unlock()
    return rc


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
