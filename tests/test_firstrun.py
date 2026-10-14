"""First run: the 1.6 GB model is downloaded with visible progress."""

import time
from pathlib import Path

import pytest

import fakes


def test_model_download_reports_progress(tmp_path, monkeypatch):
    import huggingface_hub
    import huggingface_hub.constants as hc
    import engine
    monkeypatch.setattr(hc, "HF_HUB_CACHE", str(tmp_path))
    cache = tmp_path / ("models--" + engine.MODEL_ID.replace("/", "--"))

    def fake_download(repo_id, **kw):
        cache.mkdir(parents=True)
        for i in range(4):                         # the file grows while "downloading"
            (cache / f"part{i}").write_bytes(b"\0" * (engine.MODEL_BYTES // 8))
            time.sleep(0.6)
        return str(cache)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_download)
    seen = []
    path = engine.Engine._download(lambda p, text: seen.append((p, text)))
    assert path == str(cache)
    assert seen and all("מוריד את מודל התמלול" in t for _, t in seen)
    assert max(p for p, _ in seen) > 0.2


def test_model_download_failure_is_readable(tmp_path, monkeypatch):
    import huggingface_hub
    import huggingface_hub.constants as hc
    import engine
    monkeypatch.setattr(hc, "HF_HUB_CACHE", str(tmp_path))

    def offline(repo_id, **kw):
        raise OSError("Max retries exceeded")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", offline)
    with pytest.raises(RuntimeError, match="בדקו את החיבור לאינטרנט"):
        engine.Engine._download(None)


def test_engine_status_shows_only_downloads_and_errors(make_window, world, msgbox):
    w = make_window(mic=fakes.REALTEK_MIC)
    w.on_engine_status(0.0, "טוען את מודל התמלול…")
    assert not w.engine_box.isVisible()                       # a few seconds: no need to show
    w.on_engine_status(0.4, "מוריד את מודל התמלול (פעם אחת בלבד)… 650 / 1544 MB")
    assert w.engine_box.isVisible() and w.engine_bar.value() == 400
    w.on_engine_status(-1.0, "לא ניתן להוריד את מודל התמלול. בדקו את החיבור לאינטרנט ונסו שוב.")
    assert w.engine_box.isVisible() and not w.engine_bar.isVisible()
    w.on_engine_status(1.0, "")
    assert not w.engine_box.isVisible()
