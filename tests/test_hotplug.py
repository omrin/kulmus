"""Hot-plug: the device list changes after `import sounddevice` (= Pa_Initialize).

Real PortAudio enumerates devices once, at Pa_Initialize. After a USB mic is plugged in, MME device
ids shift, so an index found by find_input() (and PortAudio's default input) can point to a different
or dead device until PortAudio is re-initialised (sd._terminate(); sd._initialize()). The fake models
exactly that (tests/fakes.py): a stale index opens a stream that never calls back.
"""

import pytest

import fakes
from conftest import wait_until


def replug(world):
    devs, new_default = fakes.replugged(world.os_devices)
    world.replug(devs, default_input=new_default)


def record(rec, seconds=1.0, timeout=3.0):
    rec.start()
    try:
        return wait_until(lambda: rec.seconds >= seconds, timeout)
    finally:
        rec.stop()


def test_named_mic_opened_without_replug(world, tmp_path):
    """Baseline: no hot-plug -> the selected microphone (by name, MME) is opened and delivers."""
    import recorder
    world.speed = 4
    rec = recorder.Recorder("mic", tmp_path / "r.wav", mic_name=fakes.CAMERA_MIC)
    assert record(rec)
    s = world.last_input()
    assert s.device.name == fakes.CAMERA_MIC and s.device.hostapi == fakes.MME and not s.stale


@pytest.mark.desired
def test_desired_list_inputs_sees_newly_plugged_mic(world):
    """The microphone menu (fill_mic_menu -> list_inputs) never shows a mic plugged in after the app
    started, because query_devices() returns PortAudio's start-up snapshot."""
    import recorder
    assert fakes.USB_MIC not in recorder.list_inputs()
    replug(world)
    names = recorder.list_inputs()
    assert fakes.USB_MIC in names, f"newly plugged mic missing from the menu: {names}"


@pytest.mark.desired
def test_desired_named_mic_still_opened_after_replug(world, tmp_path):
    """Saved mic = Realtek. After the USB Audio Device is plugged in, find_input() returns Realtek's
    START-UP index, which now belongs to another device -> stream opens, never calls back (0-byte WAV)."""
    import recorder
    world.speed = 4
    replug(world)
    rec = recorder.Recorder("mic", tmp_path / "r.wav", mic_name=fakes.REALTEK_MIC)
    ok = record(rec)
    s = world.last_input()
    assert s.device.name == fakes.REALTEK_MIC and not s.stale, (
        f"opened stale index {s.index}: PortAudio thinks it is {s.snap_dev.name!r}, Windows now has "
        f"{s.device.name!r} there; callbacks={s.callbacks}, recorded={rec.seconds:.2f}s")
    assert ok, f"selected mic delivered no audio ({rec.seconds:.2f}s)"


@pytest.mark.desired
def test_desired_default_mic_follows_windows_default_change(world, tmp_path):
    """Mic setting = 'Windows default'. The user switches the Windows default input (Sound settings)
    while the app is running: PortAudio's default input is the one cached at start-up, so the app keeps
    recording the OLD default although the button says 'Windows default'."""
    import recorder
    world.speed = 4
    world.replug(world.os_devices, default_input=fakes.CAMERA_MIC)
    rec = recorder.Recorder("mic", tmp_path / "r.wav", mic_name=None)
    record(rec)
    s = world.last_input()
    assert s.device.name == fakes.CAMERA_MIC, (
        f"Windows default is now {fakes.CAMERA_MIC!r} but {s.device.name!r} (start-up default) was opened")


@pytest.mark.desired
def test_desired_mainwindow_second_recording_after_replug(make_window, world, msgbox):
    """The user's story end-to-end: app started, first recording works, a USB audio device is plugged
    in, every later recording sits at 00:00 and Stop deletes it -> 'impossible to record again' until
    the app is restarted."""
    world.speed = 4
    w = make_window(mic=fakes.REALTEK_MIC)
    w.start_recording("mic")
    assert wait_until(lambda: w.recorder.seconds >= 1.2, 4), "first recording must work"
    w.stop_recording()
    assert w.recorder is None

    replug(world)
    w.start_recording("mic")
    assert w.recorder is not None
    ok = wait_until(lambda: w.recorder is not None and w.recorder.seconds >= 1.0, 4)
    s = world.last_input()
    detail = (f"timer={w.rec_time.text()!r}, opened index {s.index} = {s.device.name!r} "
              f"(start-up snapshot: {s.snap_dev.name!r}), callbacks={s.callbacks}")
    assert ok and s.device.name == fakes.REALTEK_MIC, "second recording after hot-plug captured nothing: " + detail


@pytest.mark.desired
def test_desired_mic_button_reflects_replugged_saved_mic(make_window, world, msgbox):
    """Saved mic unplugged at app start -> button says 'default'. When it is plugged back in, the label
    (and find_input) keep using the stale list, so the saved mic is never used again until restart."""
    full = list(world.os_devices)
    world.replug([d for d in full if d.name != fakes.CAMERA_MIC])
    world._take_snapshot()                                   # app starts without the camera mic
    w = make_window(mic=fakes.CAMERA_MIC)
    assert "Windows" in w.btn_mic.toolTip()                 # label falls back to 'default'
    world.replug(full)                                       # camera plugged back in
    world.speed = 4
    w.start_recording("mic")
    wait_until(lambda: w.recorder.seconds >= 0.5, 3)
    s = world.last_input()
    w.stop_recording()
    assert s.device.name == fakes.CAMERA_MIC, f"saved mic is connected again but {s.device.name!r} was opened"
