"""Optional stdlib-only bridge to the Rust frame matcher; safe NumPy fallback."""
from __future__ import annotations

import ctypes
import math
import os
import sys
from array import array
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
        affine = getattr(lib, "dubsync_best_affine", None)
        if affine is not None:
            affine.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
                               ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_double),
                               ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_size_t,
                               ctypes.POINTER(ctypes.c_double), ctypes.c_size_t, ctypes.c_double,
                               ctypes.POINTER(ctypes.c_double), ctypes.c_size_t]
            affine.restype = ctypes.c_ssize_t
        verifier = getattr(lib, "dubsync_verify_offset", None)
        if verifier is not None:
            verifier.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_double),
                                 ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint64),
                                 ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                                 ctypes.c_double, ctypes.c_double,
                                 ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t]
            verifier.restype = ctypes.c_ssize_t
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


def native_best_affine(candidates: list[tuple[int, int, int, float, float]],
                       source_count: int, hypotheses: list[tuple[float, float]],
                       tolerance: float, cancel_event=None
                       ) -> list[tuple[tuple[int, float, float], float, float]] | None:
    """Score hypotheses in cancellable Rust chunks; None requests NumPy fallback.

    Preserves Python's lexicographic score and first-wins tie behavior. One
    native call per 128 hypotheses amortizes FFI cost without starving cancel.
    """
    lib = _library()
    score = getattr(lib, "dubsync_best_affine", None) if lib is not None else None
    if score is None or not 0 < source_count <= 5000 or len(candidates) > 2 * source_count:
        return None
    if not candidates or not hypotheses:
        return []
    if not math.isfinite(tolerance) or tolerance <= 0 or any(
        not (isinstance(si, int) and 0 <= si < source_count
             and isinstance(ti, int) and 0 <= ti < 2**32
             and isinstance(dist, int) and 0 <= dist <= 255
             and math.isfinite(st) and math.isfinite(tt))
        for si, ti, dist, st, tt in candidates
    ):
        return None
    if any(not math.isfinite(slope) or not math.isfinite(offset)
           for slope, offset in hypotheses):
        return None
    source_indices = array("I", (entry[0] for entry in candidates))
    target_indices = array("I", (entry[1] for entry in candidates))
    distances = array("B", (entry[2] for entry in candidates))
    source_times = array("d", (entry[3] for entry in candidates))
    target_times = array("d", (entry[4] for entry in candidates))
    values = (
        (ctypes.c_uint32 * len(candidates)).from_buffer(source_indices),
        (ctypes.c_uint32 * len(candidates)).from_buffer(target_indices),
        (ctypes.c_uint8 * len(candidates)).from_buffer(distances),
        (ctypes.c_double * len(candidates)).from_buffer(source_times),
        (ctypes.c_double * len(candidates)).from_buffer(target_times),
    )
    best = None
    for start in range(0, len(hypotheses), 128):
        if cancel_event is not None and cancel_event.is_set():
            return None
        chunk = hypotheses[start:start + 128]
        flat = array("d", (value for hypothesis in chunk for value in hypothesis))
        input_chunk = (ctypes.c_double * len(flat)).from_buffer(flat)
        output = (ctypes.c_double * 5)()
        count = score(*values, len(candidates), source_count,
                      input_chunk, len(chunk), tolerance, output, len(output))
        if count not in (0, 1):
            return None
        if count:
            if not (1 <= output[0] <= source_count
                    and -64 <= output[1] <= 0 and -tolerance <= output[2] <= 0
                    and math.isfinite(output[3]) and math.isfinite(output[4])):
                return None
            item = ((int(output[0]), float(output[1]), float(output[2])),
                    float(output[3]), float(output[4]))
            if best is None or item[0] > best[0]:
                best = item
    return [best] if best is not None else []


def prepare_native_offset_verifier(source, target):
    """Prepare reused native input buffers for all offsets of one video pair."""
    lib = _library()
    verify = getattr(lib, "dubsync_verify_offset", None) if lib is not None else None
    if verify is None or len(source) > 5000 or len(target) > 5000:
        return None
    if not source or not target:
        return lambda _shift, _tolerance: []
    if any(not isinstance(item.phash, int) or not 0 <= item.phash < 2**64
           or not math.isfinite(item.time) for item in chain(source, target)):
        return None
    source_hashes = (ctypes.c_uint64 * len(source))(*(item.phash for item in source))
    source_times = (ctypes.c_double * len(source))(*(item.time for item in source))
    target_hashes = (ctypes.c_uint64 * len(target))(*(item.phash for item in target))
    target_times = (ctypes.c_double * len(target))(*(item.time for item in target))

    def match(shift: float, tolerance: float) -> list[tuple[int, int, int]] | None:
        if not math.isfinite(shift) or not math.isfinite(tolerance) or tolerance <= 0:
            return None
        output = (ctypes.c_uint32 * (3 * len(source)))()
        count = verify(source_hashes, source_times, len(source), target_hashes, target_times,
                       len(target), shift, tolerance, output, len(output))
        if count < 0 or count > len(source):
            return None
        result = [(int(output[3 * i]), int(output[3 * i + 1]), int(output[3 * i + 2]))
                  for i in range(count)]
        if any(source_index >= len(source) or target_index >= len(target) or distance > 18
               or (i > 0 and (source_index <= result[i - 1][0]
                              or target_index <= result[i - 1][1]))
               for i, (source_index, target_index, distance) in enumerate(result)):
            return None
        return result

    return match


def native_verify_offset(source, target, shift: float, tolerance: float
                         ) -> list[tuple[int, int, int]] | None:
    """One offset's monotone local matches; legacy binaries use Python."""
    verifier = prepare_native_offset_verifier(source, target)
    return verifier(shift, tolerance) if verifier is not None else None
