"""Optional stdlib-only bridge to the Rust frame matcher; safe NumPy fallback."""
from __future__ import annotations

import ctypes
import os
import sys
from functools import lru_cache
from itertools import chain
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
        # Optional extension: ABI 1 libraries built before this symbol still
        # accelerate monotone matching and use NumPy for feature candidates.
        candidate_scan = getattr(lib, "dubsync_feature_candidates", None)
        if candidate_scan is not None:
            candidate_scan.argtypes = lib.dubsync_match.argtypes
            candidate_scan.restype = ctypes.c_ssize_t
        offset_scan = getattr(lib, "dubsync_offset_candidates", None)
        if offset_scan is not None:
            offset_scan.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t,
                                    ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t,
                                    ctypes.POINTER(ctypes.c_uint8),
                                    ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t]
            offset_scan.restype = ctypes.c_ssize_t
        phash_pack = getattr(lib, "dubsync_phash_coefficients", None)
        if phash_pack is not None:
            phash_pack.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_size_t]
            phash_pack.restype = ctypes.c_uint64
        return lib
    except (AttributeError, OSError):
        return None


def native_pairs(source_hashes: list[int], target_hashes: list[int]) -> list[tuple[int, int]] | None:
    """Return native matches; None indicates unavailable/invalid native backend."""
    lib = _library()
    if lib is None or len(source_hashes) > 5000 or len(target_hashes) > 5000:
        return None
    if any(not isinstance(value, int) or not 0 <= value < 2**64
           for value in chain(source_hashes, target_hashes)):
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


def native_feature_candidates(source_hashes: list[int], target_hashes: list[int]
                              ) -> list[tuple[int, int, int]] | None:
    """Return up to two (source, target, Hamming) triples per source, or None.

    The extension is optional so installations with older Rust binaries can
    keep their existing DP acceleration and fall back to NumPy here.
    """
    lib = _library()
    scan = getattr(lib, "dubsync_feature_candidates", None) if lib is not None else None
    if scan is None or len(source_hashes) > 5000 or len(target_hashes) > 5000:
        return None
    if any(not isinstance(value, int) or not 0 <= value < 2**64
           for value in chain(source_hashes, target_hashes)):
        return None
    if not source_hashes or not target_hashes:
        return []
    try:
        sources = (ctypes.c_uint64 * len(source_hashes))(*source_hashes)
        targets = (ctypes.c_uint64 * len(target_hashes))(*target_hashes)
    except (OverflowError, TypeError):
        return None
    capacity = 6 * len(source_hashes)
    output = (ctypes.c_uint32 * capacity)()
    count = scan(sources, len(sources), targets, len(targets), output, capacity)
    if count < 0 or count > capacity // 3:
        return None
    return [(int(output[3 * i]), int(output[3 * i + 1]), int(output[3 * i + 2]))
            for i in range(count)]


def native_offset_candidates(source_hashes: list[int], target_hashes: list[int],
                             allowed_targets: list[bool]
                             ) -> list[tuple[int, int, int]] | None:
    """Nearest six distinct source/target/distance triples, excluding masked targets.

    Missing optional symbols or invalid inputs return None for a safe scalar
    fallback. The caller keeps the existing conservative scene/offset gates.
    """
    lib = _library()
    scan = getattr(lib, "dubsync_offset_candidates", None) if lib is not None else None
    if (scan is None or len(source_hashes) > 5000 or len(target_hashes) > 5000
            or len(allowed_targets) != len(target_hashes)):
        return None
    if any(not isinstance(value, int) or not 0 <= value < 2**64
           for value in chain(source_hashes, target_hashes)):
        return None
    if any(not isinstance(allowed, (bool, int)) or allowed not in (0, 1)
           for allowed in allowed_targets):
        return None
    if not source_hashes or not target_hashes:
        return []
    sources = (ctypes.c_uint64 * len(source_hashes))(*source_hashes)
    targets = (ctypes.c_uint64 * len(target_hashes))(*target_hashes)
    allowed = (ctypes.c_uint8 * len(allowed_targets))(*allowed_targets)
    capacity = 18 * len(source_hashes)
    output = (ctypes.c_uint32 * capacity)()
    count = scan(sources, len(sources), targets, len(targets), allowed, output, capacity)
    if count < 0 or count > capacity // 3:
        return None
    return [(int(output[3 * i]), int(output[3 * i + 1]), int(output[3 * i + 2]))
            for i in range(count)]


def native_phash_coefficients(coefficients) -> int | None:
    """Pack one contiguous 64-float32 DCT block using Rust, if available.

    The OpenCV DCT and its input validation stay in Python. An older native
    library simply uses the NumPy median/packbits implementation instead.
    """
    lib = _library()
    pack = getattr(lib, "dubsync_phash_coefficients", None) if lib is not None else None
    if pack is None:
        return None
    try:
        if (coefficients.dtype.name != "float32" or coefficients.ndim != 1
                or coefficients.size != 64 or not coefficients.flags.c_contiguous):
            return None
        # Cast the already-existing contiguous ndarray buffer; no copy.
        pointer = ctypes.cast(coefficients.ctypes.data, ctypes.POINTER(ctypes.c_float))
    except (AttributeError, TypeError, ValueError):
        return None
    result = int(pack(pointer, 64))
    return None if result == 2**64 - 1 else result
