# PyInstaller spec — builds a self-contained folder for Windows, macOS (.app) or Linux.
#   pyinstaller packaging/kulmus.spec --noconfirm
# The 1.6 GB transcription model is NOT bundled: it is downloaded on first run.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

ROOT = Path(SPECPATH).parent
NAME = "Kulmus"

datas = [
    (str(ROOT / "models" / "wespeaker.onnx"), "models"),
    (str(ROOT / "models" / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"),
     "models/sherpa-onnx-pyannote-segmentation-3-0"),
    (str(ROOT / "models" / "sherpa-onnx-pyannote-segmentation-3-0" / "LICENSE"),
     "models/sherpa-onnx-pyannote-segmentation-3-0"),
    (str(ROOT / "icon.ico"), "."),
    (str(ROOT / "icon.png"), "."),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),   # attribution required by CC BY 4.0 / Apache-2.0
]
binaries, hiddenimports = [], ["worker", "voices", "engine", "diarize", "recorder", "library", "icons"]
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers",
            "sounddevice", "_sounddevice_data", "soundcard", "huggingface_hub"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h
binaries += collect_dynamic_libs("ctranslate2")
if sys.platform.startswith("linux"):
    # sounddevice uses the system PortAudio on Linux: ship it so users don't have to install it
    import glob
    import os
    extra = os.environ.get("KULMUS_EXTRA_LIBS", "")       # e.g. libraries unpacked without root
    for pattern in ("/usr/lib/x86_64-linux-gnu/libportaudio.so*", "/usr/lib/libportaudio.so*",
                    f"{extra}/libportaudio.so*" if extra else "/nonexistent"):
        for lib in glob.glob(pattern):
            binaries.append((lib, "."))

a = Analysis(
    [str(ROOT / "hebtranscribe.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "PySide6.QtWebEngineCore",
              "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = str(ROOT / ("icon.ico" if sys.platform == "win32" else "icon.png"))
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False, icon=icon,
          upx=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)

if sys.platform == "darwin":
    app = BUNDLE(coll, name=f"{NAME}.app", icon=None, bundle_identifier="org.kulmus.app",
                 info_plist={
                     "CFBundleDisplayName": "קולמוס",
                     "NSMicrophoneUsageDescription": "ההקלטה מתבצעת מהמיקרופון לצורך תמלול, על המחשב בלבד.",
                     "NSHighResolutionCapable": True,
                 })
