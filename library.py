"""On-disk library of recordings: one folder per item with audio.ogg + item.json."""

import copy
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths

AUDIO_NAME = "audio.ogg"
WAV_NAME = "recording.wav"        # lossless recording, kept until it has been encoded to audio.ogg

# processing states ("recording" is used while the microphone is being captured)
QUEUED, PROCESSING, DONE, ERROR, RECORDING = "queued", "processing", "done", "error", "recording"


# names of the folders this app creates (see Library.create); nothing else in the library is touched
ITEM_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{4}$")


def default_root() -> Path:
    if os.environ.get("HEBTRANSCRIBE_ROOT"):
        return Path(os.environ["HEBTRANSCRIBE_ROOT"])
    docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home() / "Documents")
    return Path(docs) / "תמלולים"


def app_dir() -> Path:
    """Per-user app data (log, lock, saved voices) — independent of where the library is kept."""
    import sys
    if os.environ.get("HEBTRANSCRIBE_ROOT"):
        return Path(os.environ["HEBTRANSCRIBE_ROOT"])
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    new, old = base / "Kulmus", base / "HebTranscribe"
    if not new.exists() and old.exists():
        # renamed app: keep the saved voices and log (if the folder is in use, keep using it)
        try:
            old.rename(new)
        except OSError:
            return old
    return new


def root_problem(path: Path) -> str | None:
    """None if recordings can be stored in `path` (created if needed), otherwise a readable reason."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write-test-{uuid.uuid4().hex[:6]}"
        probe.write_bytes(b"ok")
        probe.unlink()
        return None
    except PermissionError:
        return "אין הרשאת כתיבה לתיקייה"
    except FileNotFoundError:
        return "הכונן או התיקייה לא נמצאו"
    except OSError as e:
        return str(e) or e.__class__.__name__


def _retry(fn, attempts=8, delay=0.15):
    """Windows: a file briefly held by a sync client / antivirus / media player fails with PermissionError."""
    for i in range(attempts):
        try:
            return fn()
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


class Item:
    def __init__(self, folder: Path, data: dict):
        self.folder = folder
        self.data = data
        self._lock = threading.Lock()   # item.json is saved from the UI thread and the worker thread

    # convenience accessors
    id = property(lambda s: s.data["id"])
    title = property(lambda s: s.data.get("title", ""))
    created = property(lambda s: s.data.get("created", 0.0))
    duration = property(lambda s: s.data.get("duration", 0.0))
    source = property(lambda s: s.data.get("source", "mic"))      # mic | call | file
    status = property(lambda s: s.data.get("status", DONE))
    segments = property(lambda s: s.data.setdefault("segments", []))
    speakers = property(lambda s: s.data.setdefault("speakers", {}))

    @property
    def audio_path(self) -> Path | None:
        p = self.folder / AUDIO_NAME
        return p if p.exists() else None

    @property
    def wav_path(self) -> Path | None:
        """The lossless recording, if it still holds audio (it is deleted once encoded)."""
        p = self.folder / WAV_NAME
        try:
            return p if p.stat().st_size > 44 else None
        except OSError:
            return None

    @property
    def playable_path(self) -> Path | None:
        return self.audio_path or self.wav_path

    def has_source_audio(self) -> bool:
        """Something the worker can (re)transcribe from."""
        src = self.data.get("pending_source")
        return bool(self.audio_path or self.wav_path or (src and Path(src).exists()))

    def audio_size(self) -> int:
        """Bytes of audio on disk (the library copy and/or a not-yet-encoded recording WAV)."""
        total = 0
        for p in (self.folder / AUDIO_NAME, self.folder / WAV_NAME):
            try:
                total += p.stat().st_size
            except OSError:
                pass
        return total

    def plain_text(self) -> str:
        return " ".join(s["text"] for s in self.segments)

    def save(self):
        with self._lock:
            for _ in range(5):
                try:
                    data = copy.deepcopy(self.data)
                    data.pop("partial", None)          # live preview of a running job, not a result
                    text = json.dumps(data, ensure_ascii=False, indent=1)
                    break
                except RuntimeError:        # dict mutated by the other thread mid-copy; try again
                    time.sleep(0.01)
            else:
                text = json.dumps(copy.deepcopy(self.data), ensure_ascii=False, indent=1)
            tmp = self.folder / f"item.json.{uuid.uuid4().hex[:8]}.tmp"
            tmp.write_text(text, encoding="utf-8")
            try:
                _retry(lambda: os.replace(tmp, self.folder / "item.json"))
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise


class Library:
    def __init__(self, root: Path | None = None):
        self.root = root or default_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[Item]:
        items = []
        for d in self.root.iterdir():
            if not d.is_dir():
                continue
            f = d / "item.json"
            if not f.exists():
                self._clean_orphan(d)
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("deleted"):
                # deleted while it was being processed and the app closed before the folder was removed;
                # audio first, so a file still in use keeps item.json (and the retry) for the next start
                try:
                    self.delete(Item(d, data))
                except OSError:
                    pass
                continue
            # leftovers of an interrupted encode / save; a file still held by someone is left for later
            for junk in list(d.glob("*.part")) + list(d.glob("item.json.*.tmp")):
                try:
                    junk.unlink(missing_ok=True)
                except OSError:
                    pass
            items.append(Item(d, data))
        items.sort(key=lambda i: i.created, reverse=True)
        return items

    @staticmethod
    def _clean_orphan(d: Path):
        """One of OUR item folders without item.json: remove it only if nothing in it holds audio.
        Any other folder is the user's (the library may be a general folder) and is never touched."""
        if not ITEM_DIR.match(d.name):
            return
        try:
            files = list(d.iterdir())
            if all(f.is_file() and (f.suffix in (".part", ".tmp") or (f.name == WAV_NAME and f.stat().st_size <= 44))
                   for f in files):
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass

    def create(self, source: str, title: str | None = None, original_name: str | None = None) -> Item:
        now = time.time()
        stamp = datetime.fromtimestamp(now)
        folder = self.root / (stamp.strftime("%Y-%m-%d_%H%M%S") + "_" + uuid.uuid4().hex[:4])
        folder.mkdir(parents=True)
        if not title:
            kind = {"mic": "הקלטה", "call": "שיחה", "file": "קובץ"}[source]
            title = f"{kind} {stamp.strftime('%d.%m.%Y %H:%M')}"
        item = Item(folder, {
            "id": folder.name, "title": title, "created": now, "duration": 0.0,
            "source": source, "original_name": original_name, "status": QUEUED,
            "progress": 0, "stage": "", "error": "", "warning": "", "num_speakers": 0,
            "speakers": {}, "segments": [],
        })
        item.save()
        return item

    def delete(self, item: Item):
        """Deletes the audio first and item.json last. Raises if a file is in use (e.g. open in a media
        player), leaving item.json in place so the item stays visible instead of silently half-deleted."""
        if not item.folder.exists():
            return
        for name in (AUDIO_NAME, WAV_NAME):
            p = item.folder / name
            if p.exists():
                _retry(p.unlink)
        shutil.rmtree(item.folder, ignore_errors=True)
        if item.folder.exists() and (item.folder / "item.json").exists():
            try:
                _retry(lambda: (item.folder / "item.json").unlink())
            except FileNotFoundError:
                pass
            shutil.rmtree(item.folder, ignore_errors=True)

    def move_item(self, item: Item, new_root: Path, pump=lambda: None) -> Item:
        """Moves an item folder into another library and returns the moved item. Never loses data:
        on the same drive it is one atomic rename (retried while a player/scanner briefly holds a file;
        `pump` lets the UI release it); across drives the folder is copied completely first, and only
        then is the source removed, audio first, so a failure leaves a full copy in at least one place."""
        dest = new_root / item.folder.name
        if dest.exists():
            raise FileExistsError(f"כבר קיימת תיקייה בשם {dest.name}")
        cross_drive = False
        for _ in range(25):
            try:
                os.rename(item.folder, dest)
                return Item(dest, item.data)
            except PermissionError:
                pump()
                time.sleep(0.15)
            except OSError as e:
                if getattr(e, "winerror", None) == 17 or e.errno == 18:    # not the same device
                    cross_drive = True
                    break
                raise
        if not cross_drive:
            raise PermissionError("קובץ של ההקלטה פתוח בתוכנה אחרת")
        tmp = new_root / (item.folder.name + ".moving")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(item.folder, tmp)
        os.rename(tmp, dest)                     # the new library now has a complete copy
        moved = Item(dest, item.data)
        try:
            for name in (AUDIO_NAME, WAV_NAME):
                src = item.folder / name
                if src.exists():
                    _retry(src.unlink)
            shutil.rmtree(item.folder)
        except OSError as e:
            raise RuntimeError(f"הועתקה לתיקייה החדשה, אך לא ניתן היה למחוק את המקור ({e})") from e
        return moved

    def delete_audio(self, item: Item) -> int:
        """Deletes the audio, keeps the transcript. Raises PermissionError if the file is in use."""
        size = 0
        for p in (item.audio_path, item.folder / WAV_NAME):
            if p and p.exists():
                size += p.stat().st_size
                _retry(p.unlink)
        item.data["audio_deleted"] = True
        item.data.pop("pending_source", None)
        item.save()
        return size
