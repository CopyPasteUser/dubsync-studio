"""Bounded, cancellable FFmpeg progress reader and no-clobber promotion."""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
import sys
import threading
from typing import BinaryIO, Callable


class MuxCancelled(RuntimeError):
    pass


def check_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise MuxCancelled("Export wurde abgebrochen.")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_mux(command: list[str], log: BinaryIO, on_line: Callable[[str], None],
            cancel_event=None) -> int:
    """Poll cancellation even while FFmpeg is silent or a progress line is partial."""
    check_cancelled(cancel_event)
    messages: Queue = Queue(maxsize=64)
    stopped = threading.Event()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log,
                          stdin=subprocess.DEVNULL, text=True, errors="replace",
                          bufsize=1) as process:
        assert process.stdout is not None

        def publish(value):
            while not stopped.is_set():
                try:
                    messages.put(value, timeout=.1)
                    return
                except Full:
                    pass

        def read():
            try:
                for line in process.stdout:
                    if stopped.is_set():
                        break
                    publish(line)
            except Exception as exc:
                publish(exc)
            finally:
                publish(None)

        reader = threading.Thread(target=read, name="dubsync-mux-progress", daemon=True)
        reader.start()
        try:
            while True:
                check_cancelled(cancel_event)
                try:
                    message = messages.get(timeout=.1)
                except Empty:
                    continue
                if message is None:
                    break
                if isinstance(message, Exception):
                    raise message
                on_line(message)
            # EOF is not proof of exit. Keep the wait cancellable too.
            while True:
                check_cancelled(cancel_event)
                try:
                    return process.wait(timeout=.1)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            stopped.set()
            _stop(process)
            reader.join(timeout=2)


def promote_output(temporary: Path, output: Path, *, overwrite: bool) -> None:
    """Publish a verified same-directory file, atomically respecting no-overwrite.

    Linux renameat2 supports filesystems with no hard links when they implement
    RENAME_NOREPLACE. Other POSIX hosts use an atomic hard link; unsupported
    filesystems fail closed instead of silently falling back to os.replace.
    """
    if overwrite:
        os.replace(temporary, output)
        return
    if os.name == "nt":
        os.rename(temporary, output)
        return
    if sys.platform.startswith("linux"):
        rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
        if rename is not None:
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                               ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(temporary), -100, os.fsencode(output), 1) == 0:
                return
            error = ctypes.get_errno()
            if error not in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
                raise OSError(error, os.strerror(error), str(output))
    os.link(temporary, output, follow_symlinks=False)
    # The caller's existing finally block removes the temporary name. Once
    # linked, the destination is already committed; cleanup must not undo it.
