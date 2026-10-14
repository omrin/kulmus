"""Choosing the folder where recordings and transcripts are kept."""

import os
from pathlib import Path

import pytest

import fakes
from conftest import wait_until
from test_round2 import write_wav


def _make_items(root: Path, n=2):
    import library as lib
    L = lib.Library(root)
    out = []
    for k in range(n):
        it = L.create("mic", title=f"פריט {k}")
        write_wav(it.folder / "recording.wav")
        it.data.update(status="done", segments=[{"start": 0, "end": 1, "speaker": "0", "text": f"טקסט {k}"}],
                       speakers={"0": "דובר 1"})
        it.save()
        out.append(it)
    return out


def _answer(monkeypatch, prefix):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "clickedButton",
                        lambda self: next((b for b in self.buttons() if b.text().startswith(prefix)), None))


def _wait_idle(w):
    assert wait_until(lambda: not w.processor.is_busy(), 20)


def test_switch_folder_and_move_recordings(make_window, world, msgbox, tmp_path, monkeypatch):
    root = Path(os.environ["HEBTRANSCRIBE_ROOT"])
    items = _make_items(root)
    w = make_window(mic=fakes.REALTEK_MIC)
    _wait_idle(w)
    new = tmp_path / "Drive" / "ההקלטות שלי"
    _answer(monkeypatch, "להעביר")
    assert w.switch_library(new)
    assert w.library.root == new
    assert sorted(i.title for i in w.items.values()) == ["פריט 0", "פריט 1"]
    for it in items:
        assert not it.folder.exists() and (new / it.folder.name / "item.json").exists()
    assert w.settings.value("library_root") == str(new)
    assert w.list.count() == 2


def test_switch_folder_keep_recordings_then_switch_back(make_window, world, msgbox, tmp_path, monkeypatch):
    root = Path(os.environ["HEBTRANSCRIBE_ROOT"])
    _make_items(root)
    w = make_window(mic=fakes.REALTEK_MIC)
    _wait_idle(w)
    new = tmp_path / "other"
    _answer(monkeypatch, "להשאיר")
    assert w.switch_library(new)
    assert w.items == {} and w.list.count() == 0
    # new recordings go to the new folder
    world.speed = 4
    w.start_recording("mic")
    assert wait_until(lambda: w.recorder.seconds >= 1.2, 5)
    w.stop_recording()
    rec = w.current
    assert rec.folder.parent == new
    _wait_idle(w)
    _answer(monkeypatch, "להשאיר")
    assert w.switch_library(root)
    assert sorted(i.title for i in w.items.values()) == ["פריט 0", "פריט 1"]


def test_cannot_pick_a_folder_inside_or_around_the_current_one(make_window, world, msgbox):
    w = make_window(mic=fakes.REALTEK_MIC)
    root = w.library.root
    assert not w.switch_library(root / "sub")
    assert not w.switch_library(root.parent)
    assert w.library.root == root
    assert len(msgbox.kinds("warning")) == 2


def test_cannot_switch_while_recording(make_window, world, msgbox, tmp_path):
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    assert not w.switch_library(tmp_path / "x")
    assert msgbox.kinds("information")
    w.stop_recording()


def test_unavailable_saved_folder_falls_back_with_a_warning(make_window, world, msgbox, monkeypatch):
    w = make_window(mic=fakes.REALTEK_MIC)
    w.settings.setValue("library_root", r"Q:\לא-קיים\הקלטות")
    import library as lib
    monkeypatch.delenv("HEBTRANSCRIBE_ROOT")
    root, warning = w.resolve_library_root()
    assert root == lib.default_root()
    assert "אינה זמינה" in warning and r"Q:\לא-קיים\הקלטות" in warning


def test_cleanup_never_touches_the_users_own_folders(tmp_path):
    """The library may be a general folder (e.g. Documents): only our own item folders may be removed."""
    import library as lib
    (tmp_path / "My Stuff").mkdir()                         # an empty folder of the user
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "x.part").write_bytes(b"1")      # looks like our junk, but isn't ours
    ours = tmp_path / "2026-10-08_101450_66fc"              # our orphan: only an empty WAV
    ours.mkdir()
    (ours / "recording.wav").write_bytes(b"")
    lib.Library(tmp_path).load_all()
    assert (tmp_path / "My Stuff").exists() and (tmp_path / "photos" / "x.part").exists()
    assert not ours.exists()


def test_cross_drive_move_copies_completely_then_removes_source(tmp_path, monkeypatch):
    """Different drive: os.rename fails with WinError 17 -> copy, then delete the source."""
    import library as lib
    src_root, dst_root = tmp_path / "a", tmp_path / "b"
    (it,) = _make_items(src_root, 1)
    dst_root.mkdir()
    real_rename = os.rename

    def rename(a, b):
        if Path(a) == it.folder:
            e = OSError(18, "not same device")
            e.winerror = 17
            raise e
        return real_rename(a, b)

    monkeypatch.setattr(lib.os, "rename", rename)
    moved = lib.Library(src_root).move_item(it, dst_root)
    assert (moved.folder / "item.json").exists() and (moved.folder / "recording.wav").exists()
    assert not it.folder.exists()
    assert not list(dst_root.glob("*.moving"))
