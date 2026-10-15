"""Runs the heavy work (transcription, diarization, audio encoding) away from the UI process.

Transcription and diarization keep the Python GIL busy for long stretches (seconds to minutes). In the UI
process that froze the window and starved the recorder's audio callback, so audio recorded meanwhile
was lost. Everything heavy therefore runs in a child process at below-normal priority, which also
makes "cancel" and "quit" instant: the child is simply killed.

Jobs: ("process", {path, source, num_speakers}) -> (segments, speakers, duration)
      ("wav_to_opus", {wav, out})               -> duration
      ("convert", {src, out})                   -> duration
Child -> parent messages: ("progress", p, stage) | ("partial", segments) | ("result", value) | ("error", text, tb)
"""

import os
import sys
import traceback
from pathlib import Path

from engine import Cancelled, NoAudio


class JobError(RuntimeError):
    """A job failed. `permanent`: retrying the same input cannot succeed (e.g. no audio track)."""

    def __init__(self, text: str, permanent: bool = False):
        super().__init__(text)
        self.permanent = permanent


def friendly_job_error(e: BaseException) -> str:
    name = e.__class__.__name__
    s = str(e).strip()
    if isinstance(e, NoAudio):
        return str(e)
    if name.startswith("InvalidDataError") or "Invalid data found" in s:
        return "לא ניתן לקרוא את קובץ השמע (פגום או בפורמט לא נתמך)"
    if isinstance(e, MemoryError):
        return "אין מספיק זיכרון לעיבוד הקובץ"
    if isinstance(e, FileNotFoundError):
        return "קובץ המקור לא נמצא (ייתכן שהועבר או נמחק)"
    if isinstance(e, OSError) and getattr(e, "winerror", None) in (5, 32, 33):
        return "הקובץ בשימוש על ידי תוכנה אחרת"
    if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
        return "אין מספיק מקום פנוי בדיסק"
    return s or name


def run_job(kind, args, engine, on_progress, on_partial, cancelled):
    from engine import convert_to_opus
    from recorder import wav_to_opus
    if kind == "warmup":
        engine.whisper(on_progress)   # load (first run: download) the model before a job needs it
        return None
    if kind == "process":
        engine.profiles = args.get("profiles") or {}
        engine.last_voiceprints = {}
        segs, speakers, duration = engine.process(Path(args["path"]), args["source"], args["num_speakers"],
                                                  on_progress=on_progress, on_partial=on_partial,
                                                  cancelled=cancelled)
        return segs, speakers, duration, getattr(engine, "last_voiceprints", {}) or {}
    if kind == "wav_to_opus":
        return wav_to_opus(Path(args["wav"]), Path(args["out"]), cancelled=cancelled)
    if kind == "convert":
        return convert_to_opus(args["src"], Path(args["out"]),
                               progress=lambda p: on_progress(p, "שומר עותק של הקובץ…"), cancelled=cancelled)
    raise ValueError(f"unknown job {kind!r}")


# --------------------------------------------------------------------------- child process

def _below_normal_priority():
    if sys.platform == "win32":
        import ctypes
        BELOW_NORMAL_PRIORITY_CLASS = 0x4000
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
    else:
        try:
            os.nice(5)
        except Exception:
            pass


def _die_with_parent():
    """multiprocessing's daemon flag only helps on a clean exit of the parent. If the app crashes or is
    killed, exit too instead of encoding on as an orphan that holds the library's files open."""
    import multiprocessing
    import threading
    from multiprocessing.connection import wait
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def watch():
        wait([parent.sentinel])
        os._exit(0)

    threading.Thread(target=watch, daemon=True, name="parent-watch").start()


def worker_main(conn, log_path: str | None = None):
    """Child process entry point."""
    _die_with_parent()
    if log_path and (sys.stdout is None or sys.stderr is None):
        # pythonw: no console; libraries that print (tqdm) would crash on None
        log = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    import faulthandler
    if sys.stderr is not None:
        faulthandler.enable(file=sys.stderr)
    _below_normal_priority()
    from engine import Engine
    engine = Engine()
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        if msg is None:
            return
        kind, args = msg
        try:
            res = run_job(kind, args, engine,
                          on_progress=lambda p, stage: conn.send(("progress", p, stage)),
                          on_partial=lambda segs: conn.send(("partial", segs)),
                          cancelled=lambda: False)     # the parent cancels by killing this process
            conn.send(("result", res))
        except Exception as e:
            traceback.print_exc()
            conn.send(("error", friendly_job_error(e), traceback.format_exc(), isinstance(e, NoAudio)))


# --------------------------------------------------------------------------- parent side

class InProcessBackend:
    """Runs jobs in the calling thread. Used by the test-suite (which stubs Engine.process)."""

    def __init__(self):
        from engine import Engine
        self.engine = Engine()

    def call(self, kind, args, on_progress, on_partial, cancelled):
        try:
            return run_job(kind, args, self.engine, on_progress, on_partial, cancelled)
        except Cancelled:
            raise
        except Exception as e:
            traceback.print_exc()
            raise JobError(friendly_job_error(e), isinstance(e, NoAudio)) from e

    def shutdown(self):
        pass


def _kill_on_close_job():
    """A Windows job object that kills its processes when the last handle to it closes, i.e. when this
    (UI) process exits for any reason, including a crash or Task Manager. Returns a handle or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps",
                                                        "ReadBytes", "WriteBytes", "OtherBytes")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED()
        info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(wintypes.HANDLE(job))
            return None
        return job
    except Exception:
        traceback.print_exc()
        return None


def _assign_to_job(job, pid):
    if not job:
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x0100 | 0x0001, False, pid)      # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if h:
            k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(h))
            k32.CloseHandle(wintypes.HANDLE(h))
    except Exception:
        traceback.print_exc()


class SubprocessBackend:
    """Runs jobs in a persistent child process (the whisper model stays loaded between jobs).
    Cancelling kills the child; the next job starts a fresh one."""

    def __init__(self, log_path: str | None = None, target=worker_main):
        import multiprocessing as mp
        self.ctx = mp.get_context("spawn")
        self.log_path = log_path
        self.target = target
        self.proc = None
        self.conn = None
        self._job = _kill_on_close_job()

    def _ensure(self):
        if self.proc is None or not self.proc.is_alive():
            self._kill()
            parent, child = self.ctx.Pipe()
            proc = self.ctx.Process(target=self.target, args=(child, self.log_path), daemon=True,
                                    name="kulmus-worker")
            proc.start()
            _assign_to_job(self._job, proc.pid)
            child.close()
            self.proc, self.conn = proc, parent

    def _kill(self):
        proc, conn = self.proc, self.conn
        self.proc = self.conn = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if proc is not None:
            try:
                if proc.is_alive():
                    proc.kill()
                proc.join(5)
            except Exception:
                pass

    def call(self, kind, args, on_progress, on_partial, cancelled):
        """Blocks the calling (worker) thread until the job ends. Polls `cancelled` every 100 ms."""
        if cancelled():
            raise Cancelled()
        self._ensure()
        proc, conn = self.proc, self.conn
        try:
            conn.send((kind, args))
        except (OSError, EOFError):
            self._kill()
            raise JobError("תהליך העיבוד נסגר באופן בלתי צפוי")
        while True:
            if cancelled():
                self._kill()
                raise Cancelled()
            try:
                ready = conn.poll(0.1)
            except (OSError, EOFError):
                ready = False
            if not ready:
                if not proc.is_alive():
                    code = proc.exitcode
                    self._kill()
                    raise JobError(f"תהליך העיבוד קרס (קוד {code}). ייתכן שאין מספיק זיכרון.")
                continue
            try:
                msg = conn.recv()
            except (OSError, EOFError):
                code = proc.exitcode
                self._kill()
                raise JobError(f"תהליך העיבוד קרס (קוד {code}).")
            tag = msg[0]
            if tag == "progress":
                on_progress(msg[1], msg[2])
            elif tag == "partial":
                on_partial(msg[1])
            elif tag == "result":
                return msg[1]
            elif tag == "error":
                raise JobError(msg[1], bool(msg[3]) if len(msg) > 3 else False)

    def shutdown(self):
        conn = self.conn
        if conn is not None:
            try:
                conn.send(None)
            except Exception:
                pass
        self._kill()


def make_backend(log_path: str | None = None):
    if os.environ.get("HEBTRANSCRIBE_INPROCESS"):
        return InProcessBackend()
    return SubprocessBackend(log_path)
