"""Hardware-free stand-ins for `sounddevice` (PortAudio) and `soundcard` (WASAPI).

The fake PortAudio keeps two device lists, like the real thing does:
  * `world.os_devices`  – what Windows has right now (changes on hot-plug via `world.replug()`);
  * `world.snapshot`    – what PortAudio enumerated at Pa_Initialize (= `import sounddevice`).
`query_devices()` / `default.device` / device indices all come from the *snapshot*, exactly like real
PortAudio. The snapshot is refreshed only when the PortAudio reference count goes 0 -> 1, i.e. by
`sd._terminate(); sd._initialize()` (the documented way to re-enumerate in python-sounddevice).

Opening an index whose snapshot entry no longer matches the device Windows has at that position
(stale index after a hot-plug) yields a stream that starts fine but NEVER calls back – this is what the
py-spy dump of the user's app showed (writer alive, recording.wav 0 bytes, no error).
"""

import threading
import time
import types

import numpy as np

RATE = 48_000
MME, WASAPI = 0, 2
HOSTAPI_NAMES = {0: "MME", 1: "Windows DirectSound", 2: "Windows WASAPI", 3: "Windows WDM-KS"}


class PortAudioError(Exception):
    pass


class CallbackFlags:
    input_overflow = False
    input_underflow = False
    output_overflow = False
    output_underflow = False

    def __init__(self, input_overflow=False):
        self.input_overflow = input_overflow

    def __bool__(self):
        return bool(self.input_overflow)


class Dev:
    """A physical endpoint as Windows exposes it on one host API."""

    def __init__(self, name, ins=1, outs=0, hostapi=MME, signal="tone", amp=0.5, freq=440.0):
        self.name, self.ins, self.outs, self.hostapi = name, ins, outs, hostapi
        self.signal = signal          # tone | silent | dead   (dead = stream opens, never calls back)
        self.amp, self.freq = amp, freq
        self.unplug_after = None      # seconds of audio after which callbacks stop (device yanked)

    def samples(self, n, offset):
        if self.signal == "silent":
            return np.zeros(n, np.float32)
        t = (offset + np.arange(n)) / RATE
        return (self.amp * np.sin(2 * np.pi * self.freq * t)).astype(np.float32)

    def __repr__(self):
        return f"<Dev {self.name!r} api={self.hostapi} {self.signal}>"


# --------------------------------------------------------------------------- realistic device sets

REALTEK_MIC = "מיקרופון (Realtek(R) Audio)"
CAMERA_MIC = "Microphone (USB 2.0 Camera)"
USB_MIC = "Microphone (USB Audio Device)"
SPEAKERS = "רמקולים (Realtek(R) Audio)"


def machine_before_replug():
    """Devices at app start (10:20): Realtek + camera mic, no USB Audio Device."""
    return [
        Dev("Microsoft Sound Mapper - Input", ins=2, signal="tone"),
        Dev(REALTEK_MIC, ins=2, freq=440.0, amp=0.5),
        Dev(CAMERA_MIC, ins=2, freq=550.0, amp=0.6),
        Dev("Microsoft Sound Mapper - Output", ins=0, outs=2),
        Dev(SPEAKERS, ins=0, outs=2),
        # the same endpoints again on WASAPI (find_input must ignore these)
        Dev(SPEAKERS, ins=0, outs=2, hostapi=WASAPI),
        Dev(REALTEK_MIC, ins=2, hostapi=WASAPI),
        Dev(CAMERA_MIC, ins=2, hostapi=WASAPI),
    ]


def replugged(before):
    """Same machine after the USB Audio Device was plugged in: MME ids shift, the new (silent)
    device becomes the Windows default input. Existing Dev objects are reused (same physical device)."""
    by = {(d.name, d.hostapi): d for d in before}
    usb = Dev(USB_MIC, ins=1, signal="tone", amp=0.00006, freq=300.0)
    usb_w = Dev(USB_MIC, ins=1, hostapi=WASAPI, amp=0.00006)
    return [
        by[("Microsoft Sound Mapper - Input", MME)],
        usb,
        by[(REALTEK_MIC, MME)],
        by[(CAMERA_MIC, MME)],
        by[("Microsoft Sound Mapper - Output", MME)],
        by[(SPEAKERS, MME)],
        by[(SPEAKERS, WASAPI)],
        usb_w,
        by[(REALTEK_MIC, WASAPI)],
        by[(CAMERA_MIC, WASAPI)],
    ], USB_MIC


# --------------------------------------------------------------------------- the simulated machine

class World:
    def __init__(self, devices=None, default_input=REALTEK_MIC, default_output=SPEAKERS, speed=1.0):
        self.os_devices = list(devices if devices is not None else machine_before_replug())
        self.os_default_in, self.os_default_out = default_input, default_output
        self.speed = speed                    # >1: fake devices deliver faster than real time
        # MME/PortAudio input buffering: if the callback can't run (e.g. another thread holds the GIL)
        # for longer than this, the driver runs out of buffers and audio is DROPPED (input overflow).
        self.input_buffer_s = 0.3
        self.refcount = 1                     # PortAudio already initialised by "import sounddevice"
        self.reinit_count = 0
        self._take_snapshot()
        self.streams = []                     # every stream ever constructed
        # fault injection
        self.input_open_error = None          # raised by InputStream(...)
        self.input_start_error = None         # raised by InputStream.start()
        self.input_stop_error = None          # raised by InputStream.stop()
        self.output_open_error = None         # raised by OutputStream(...)
        # soundcard (WASAPI loopback) side
        self.speaker_name = SPEAKERS
        self.loopback_signal = "tone"         # tone | silent
        self.loopback_amp, self.loopback_freq = 0.3, 660.0
        self.sc_get_microphone_error = None   # raised by sc.get_microphone()
        self.sc_recorder_error = None         # raised by mic.recorder() (soundcard's _AudioClient asserts)
        self.sc_record_error = None           # raised by r.record() ...
        self.sc_record_error_after = 0.0      # ... after this many seconds of loopback audio
        self.loopback_recorders = []

    # -- PortAudio enumeration
    def _take_snapshot(self):
        self.snapshot = list(self.os_devices)
        self.snap_default_in = self._index_of(self.os_default_in, inputs=True)
        self.snap_default_out = self._index_of(self.os_default_out, inputs=False)

    def _index_of(self, name, inputs):
        for i, d in enumerate(self.snapshot):
            if d.hostapi == MME and d.name == name and (d.ins if inputs else d.outs) > 0:
                return i
        return -1

    def pa_initialize(self):
        if self.refcount == 0:
            self._take_snapshot()
            self.reinit_count += 1
        self.refcount += 1

    def pa_terminate(self):
        if self.refcount <= 0:
            raise PortAudioError("Error terminating PortAudio: PortAudio not initialized [PaErrorCode -10000]")
        self.refcount -= 1
        if self.refcount == 0:
            for s in self.streams:          # Pa_Terminate closes every open stream
                s._halt()
                s.closed = True

    # -- hot-plug
    def replug(self, devices, default_input=None, default_output=None):
        self.os_devices = list(devices)
        if default_input is not None:
            self.os_default_in = default_input
        if default_output is not None:
            self.os_default_out = default_output

    def unplug(self, name):
        self.replug([d for d in self.os_devices if d.name != name])

    # -- introspection helpers for tests
    @property
    def input_streams(self):
        return [s for s in self.streams if s.kind == "input"]

    @property
    def output_streams(self):
        return [s for s in self.streams if s.kind == "output"]

    def last_input(self):
        return self.input_streams[-1] if self.input_streams else None


class _Stream:
    def __init__(self, world, kind, samplerate=None, channels=1, dtype="float32", blocksize=0,
                 device=None, callback=None, finished_callback=None, **kw):
        self.world, self.kind = world, kind
        self.samplerate = samplerate or RATE
        self.channels = channels
        self.blocksize = blocksize or RATE // 100
        self.callback = callback
        self.finished_callback = finished_callback
        self.requested_device = device
        self.callbacks = 0
        self.frames_delivered = 0
        self.dropped_frames = 0               # audio lost to input overflow (callback starved)
        self.overflows = 0
        self.started = self.closed = False
        self._stop_evt = threading.Event()
        self._thread = None
        idx = self._resolve(device)
        self.index = idx
        self.snap_dev = world.snapshot[idx]
        phys = world.os_devices[idx] if idx < len(world.os_devices) else None
        self.stale = phys is not self.snap_dev
        if phys is None:
            raise PortAudioError(f"Error opening {self.__class__.__name__}: Unanticipated host error "
                                 f"[PaErrorCode -9999]: 'Undefined external error.' [MME error 2]")
        # a stale index binds to whatever Windows has at that position now, but delivers nothing
        self.device = phys
        world.streams.append(self)

    # sounddevice-style device resolution against the PortAudio snapshot
    def _resolve(self, device):
        w = self.world
        want_in = self.kind == "input"
        if device is None or device == -1:
            idx = w.snap_default_in if want_in else w.snap_default_out
            if idx < 0:
                raise PortAudioError("Error querying device -1")
            return idx
        if isinstance(device, (int, np.integer)):
            if not 0 <= device < len(w.snapshot):
                raise PortAudioError(f"Error querying device {device}")
            return int(device)
        if isinstance(device, str):
            matches = []
            for i, d in enumerate(w.snapshot):
                if (d.ins if want_in else d.outs) == 0:
                    continue
                full = f"{d.name}, {HOSTAPI_NAMES[d.hostapi]}"
                if device == d.name or device.lower() in full.lower():
                    matches.append(i)
            if not matches:
                raise ValueError(f"No {'input' if want_in else 'output'} device matching {device!r}")
            exact = [i for i in matches if w.snapshot[i].name == device]
            if len(matches) > 1 and len(exact) != 1:
                raise ValueError(f"Multiple {'input' if want_in else 'output'} devices found for {device!r}")
            return exact[0] if exact else matches[0]
        raise TypeError(f"bad device {device!r}")

    @property
    def active(self):
        return self.started and not self.closed and not self._stop_evt.is_set()

    @property
    def stopped(self):
        return not self.active

    def start(self):
        if self.closed:
            raise PortAudioError("Error starting stream: Invalid stream pointer [PaErrorCode -9988]")
        self.started = True
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"fake-{self.kind}")
        self._thread.start()

    def _halt(self):
        self._stop_evt.set()
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout=2)

    def stop(self, ignore_errors=True):
        self._halt()

    def abort(self, ignore_errors=True):
        self._halt()

    def close(self, ignore_errors=True):
        self._halt()
        self.closed = True

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *a):
        self.stop()
        self.close()

    def _alive_source(self):
        """The physical device if it is still delivering, else None."""
        d = self.device
        if self.stale or d is None or d.signal == "dead":
            return None
        if d not in self.world.os_devices:        # unplugged mid-stream
            return None
        if d.unplug_after is not None and self.frames_delivered >= d.unplug_after * RATE:
            return None
        return d

    def _run(self):
        n = self.blocksize
        next_t = time.perf_counter()
        while not self._stop_evt.is_set():
            period = n / self.samplerate / max(self.world.speed, 1e-6)
            if self.kind == "input":
                src = self._alive_source()
                lag = time.perf_counter() - next_t
                overflow = False
                if src is not None and lag > self.world.input_buffer_s:
                    # callback starved longer than the driver can buffer: those frames are gone
                    drop = int((lag - self.world.input_buffer_s) * self.samplerate * max(self.world.speed, 1e-6))
                    drop -= drop % n
                    if drop:
                        overflow = True
                        self.dropped_frames += drop
                        self.frames_delivered += drop
                        self.overflows += 1
                        next_t += drop / self.samplerate / max(self.world.speed, 1e-6)
                if src is not None:
                    mono = src.samples(n, self.frames_delivered)
                    data = np.repeat(mono[:, None], self.channels, axis=1)
                    self.callback(data, n, None, CallbackFlags(overflow))
                    self.callbacks += 1
                    self.frames_delivered += n
            else:
                out = np.full((n, self.channels), 0.123, np.float32)
                if self.callback:
                    self.callback(out, n, None, CallbackFlags())
                self.callbacks += 1
            next_t += period
            self._stop_evt.wait(max(0.0, next_t - time.perf_counter()))


class _InputStream(_Stream):
    def __init__(self, world, *a, **kw):
        if world.input_open_error is not None:
            raise world.input_open_error
        super().__init__(world, "input", *a, **kw)

    def start(self):
        if self.world.input_start_error is not None:
            raise self.world.input_start_error
        super().start()

    def stop(self, ignore_errors=True):
        super().stop()
        if self.world.input_stop_error is not None:
            raise self.world.input_stop_error


class _OutputStream(_Stream):
    def __init__(self, world, *a, **kw):
        if world.output_open_error is not None:
            raise world.output_open_error
        super().__init__(world, "output", *a, **kw)


def make_sounddevice(world: World) -> types.ModuleType:
    m = types.ModuleType("sounddevice")
    m.__version__ = "0.5.6-fake"
    m.__file__ = "<fake sounddevice>"
    m.PortAudioError = PortAudioError
    m.CallbackFlags = CallbackFlags

    class CallbackStop(Exception):
        pass

    class CallbackAbort(Exception):
        pass

    m.CallbackStop, m.CallbackAbort = CallbackStop, CallbackAbort
    m.InputStream = lambda *a, **kw: _InputStream(world, *a, **kw)
    m.OutputStream = lambda *a, **kw: _OutputStream(world, *a, **kw)
    m._initialize = world.pa_initialize
    m._terminate = world.pa_terminate

    def info(i):
        d = world.snapshot[i]
        return {"name": d.name, "index": i, "hostapi": d.hostapi,
                "max_input_channels": d.ins, "max_output_channels": d.outs,
                "default_low_input_latency": 0.09, "default_low_output_latency": 0.09,
                "default_high_input_latency": 0.18, "default_high_output_latency": 0.18,
                "default_samplerate": 44100.0}

    class DeviceList(tuple):
        pass

    def query_devices(device=None, kind=None):
        if world.refcount <= 0:
            raise PortAudioError("Error querying device: PortAudio not initialized [PaErrorCode -10000]")
        if kind is not None and device is None:
            idx = world.snap_default_in if kind == "input" else world.snap_default_out
            if idx < 0:
                raise PortAudioError(f"Error querying device {idx}")
            return info(idx)
        if device is None:
            return DeviceList(info(i) for i in range(len(world.snapshot)))
        if isinstance(device, str):
            probe = _Stream.__new__(_Stream)
            probe.world, probe.kind = world, kind or "input"
            return info(_Stream._resolve(probe, device))
        return info(int(device))

    m.query_devices = query_devices

    def query_hostapis(index=None):
        apis = [{"name": HOSTAPI_NAMES[k], "devices": [i for i, d in enumerate(world.snapshot) if d.hostapi == k],
                 "default_input_device": world.snap_default_in if k == MME else -1,
                 "default_output_device": world.snap_default_out if k == MME else -1}
                for k in sorted(HOSTAPI_NAMES)]
        return apis if index is None else apis[index]

    m.query_hostapis = query_hostapis

    class _Default:
        hostapi = MME
        samplerate = None
        blocksize = 0
        dtype = ("float32", "float32")
        channels = (None, None)
        latency = ("high", "high")

        @property
        def device(self):
            return [world.snap_default_in, world.snap_default_out]

        @device.setter
        def device(self, v):
            raise NotImplementedError("fake")

    m.default = _Default()
    return m


# --------------------------------------------------------------------------- soundcard (WASAPI loopback)

class _Speaker:
    def __init__(self, name):
        self.name = name
        self.id = "{0.0.0.00000000}.{fake-" + name + "}"
        self.channels = 2


class _LoopbackRecorder:
    def __init__(self, world, samplerate, channels, blocksize):
        self.world, self.samplerate, self.channels = world, samplerate, channels or 2
        self.frames = 0
        self.entered = self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *a):
        self.exited = True

    def record(self, numframes=None):
        w = self.world
        n = numframes or 480
        if w.sc_record_error is not None and self.frames >= w.sc_record_error_after * self.samplerate:
            raise w.sc_record_error
        time.sleep(n / self.samplerate / max(w.speed, 1e-6))
        if w.loopback_signal == "silent":
            mono = np.zeros(n, np.float32)
        else:
            t = (self.frames + np.arange(n)) / self.samplerate
            mono = (w.loopback_amp * np.sin(2 * np.pi * w.loopback_freq * t)).astype(np.float32)
        self.frames += n
        return np.repeat(mono[:, None], self.channels, axis=1).astype(np.float32)


class _LoopbackMic:
    def __init__(self, world, name):
        self.world, self.name = world, name
        self.isloopback = True
        self.channels = 2

    def recorder(self, samplerate, channels=None, blocksize=None, exclusive_mode=False):
        if self.world.sc_recorder_error is not None:
            raise self.world.sc_recorder_error
        r = _LoopbackRecorder(self.world, samplerate, channels, blocksize)
        self.world.loopback_recorders.append(r)
        return r


def make_soundcard(world: World) -> types.ModuleType:
    m = types.ModuleType("soundcard")
    m.__file__ = "<fake soundcard>"
    m.default_speaker = lambda: _Speaker(world.speaker_name)

    def get_microphone(id, include_loopback=False):
        if world.sc_get_microphone_error is not None:
            raise world.sc_get_microphone_error
        if not include_loopback:
            raise IndexError(f"no device with id {id}")
        return _LoopbackMic(world, id)

    m.get_microphone = get_microphone
    m.all_microphones = lambda include_loopback=False: []
    m.all_speakers = lambda: [_Speaker(world.speaker_name)]
    return m
