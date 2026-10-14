"""Child-process worker for the starvation test: 'process' jobs hold the GIL for 2 s (like sherpa-onnx's
diarization); every other job is the real implementation."""

import ctypes
import time


def main(conn, log_path=None):
    import worker
    hold_gil = ctypes.PyDLL("kernel32").Sleep
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        if msg is None:
            return
        kind, args = msg
        try:
            if kind == "process":
                conn.send(("progress", 0.0, "מתמלל…"))
                time.sleep(0.5)
                conn.send(("progress", 0.75, "מזהה דוברים…"))
                hold_gil(2000)                     # holds THIS process's GIL only
                conn.send(("result", ([], {}, 1.0)))
            else:
                res = worker.run_job(kind, args, None, lambda p, s: None, lambda s: None, lambda: False)
                conn.send(("result", res))
        except Exception as e:
            conn.send(("error", str(e), ""))
