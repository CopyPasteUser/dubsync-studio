"""Optional stdlib-only bridge to the Rust frame matcher; safe NumPy fallback."""
from __future__ import annotations

import ctypes
import os
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _library():
    if os.environ.get("DUBSYNC_NATIVE") == "0":
        return None
    name = ("dubsync_native.dll" if sys.platform == "win32" else
            "libdubsync_native.dylib" if sys.platform == "darwin" else
            "libdubsync_native.so")
    path = Path(__file__).resolve().parents[1] / "_native" / name
    if not path.is_file():
        return None
    try:
        lib = ctypes.CDLL(str(path))
        lib.dubsync_native_abi.restype = ctypes.c_uint32
        if lib.dubsync_native_abi() != 1:
            return None
        lib.dubsync_match.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t]
        lib.dubsync_match.restype = ctypes.c_ssize_t
        return lib
    except (AttributeError, OSError):
        return None


def native_pairs(source_hashes: list[int], target_hashes: list[int]) -> list[tuple[int, int]] | None:
    """Return native matches; None indicates unavailable/invalid native backend."""
    lib = _library()
    if lib is None or len(source_hashes) > 5000 or len(target_hashes) > 5000:
        return None
    if any(not isinstance(value, int) or not 0 <= value < 2**64
           for value in (*source_hashes, *target_hashes)):
        return None
    if not source_hashes or not target_hashes:
        return []
    try:
        sources = (ctypes.c_uint64 * len(source_hashes))(*source_hashes)
        targets = (ctypes.c_uint64 * len(target_hashes))(*target_hashes)
    except (OverflowError, TypeError):
        return None
    capacity = 2 * min(len(source_hashes), len(target_hashes))
    output = (ctypes.c_uint32 * capacity)()
    count = lib.dubsync_match(sources, len(sources), targets, len(targets), output, capacity)
    if count < 0 or count > capacity // 2:
        return None
    return [(output[2 * i], output[2 * i + 1]) for i in range(count)]
