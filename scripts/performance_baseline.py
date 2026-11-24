"""Measure current DubSync behavior in isolated, disposable XDG state (Linux).

No export, no source mutation, no persistent application settings or caches.
Cold means an empty DubSync frame cache, not an empty OS page cache.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def isolate(directory: Path) -> None:
    for name in ("CONFIG", "CACHE", "STATE", "DATA"):
        os.environ[f"XDG_{name}_HOME"] = str(directory / name.lower())


def pss_mib() -> float | None:
    try:
        for line in Path("/proc/self/smaps_rollup").read_text().splitlines():
            if line.startswith("Pss:"):
                return round(int(line.split()[1]) / 1024, 3)
    except (OSError, ValueError, IndexError):
        pass
    return None


def summarize(values: list[float]) -> dict:
    if not values:
        raise ValueError("No measurements")
    return {"samples": values, "median": statistics.median(values),
            "min": min(values), "max": max(values)}


def cpu_seconds() -> float:
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime


def evidence(result) -> dict:
    data = asdict(result)
    data.pop("source_path", None)
    data.pop("target_path", None)
    data["safe_for_automatic_export"] = result.safe_for_automatic_export
    return data


def revision() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def alignment_trial(source: Path, master: Path, args) -> tuple[dict, dict]:
    from dubsync.core.alignment import align_videos

    diagnostics = []
    cpu_start = cpu_seconds()
    started = time.perf_counter()
    result = align_videos(str(source), str(master), interval=args.interval,
                          max_samples=args.max_samples,
                          hardware_accel=args.decoder, sampling_workers=args.workers,
                          frame_cache_enabled=True, diagnostic_sink=diagnostics.append)
    wall = time.perf_counter() - started
    cpu = cpu_seconds() - cpu_start
    if len(diagnostics) != 1:
        raise RuntimeError("Missing alignment diagnostic")
    report = diagnostics[0]
    stages = report["stages"]
    profile = stages["pipeline_profile"]
    if not profile["complete"]:
        raise RuntimeError("Incomplete pipeline measurement")
    sample = {}
    for side in ("source", "master"):
        info = stages.get(f"sample_{side}", {})
        sample[side] = {key: info.get(key) for key in (
            "status", "backend", "selected_decoder", "samples_collected",
            "gpu_benchmark_seconds", "decode_seconds")}
    return {
        "wall_seconds": wall, "cpu_seconds_including_waited_children": cpu,
        "parent_pss_mib": pss_mib(),
        "pipeline": {k: profile[k] for k in (
            "probe_wall_seconds", "sampling_wall_seconds", "matching_wall_seconds",
            "longest_phase", "worker_count")},
        "sampling": sample, "method": report["decision"]["method"],
        "safe_for_automatic_export": result.safe_for_automatic_export,
    }, evidence(result)


def alignment_suite(directory: Path, args) -> dict:
    from scripts.benchmark_pipeline import _synthetic_video
    from scripts.benchmark_match_pipeline import _synthetic_shift
    from dubsync.core.performance import system_specs

    from dubsync.core.native_match import _library

    if args.source is not None:
        source, master = args.source.resolve(strict=True), args.master.resolve(strict=True)
        if not source.is_file() or not master.is_file():
            raise ValueError("Both inputs must be regular files")
        if source.samefile(master):
            raise ValueError("Use two distinct input files for cold/warm measurement")
        scenario = "supplied"
    else:
        source, master = directory / "source.mkv", directory / "master.mkv"
        if args.scenario == "shifted":
            _synthetic_shift(source, master, args.duration)
        else:
            _synthetic_video(source, args.duration)
            shutil.copyfile(source, master)
        scenario = args.scenario
    reference = None
    runs = {"cold": [], "warm": []}
    for index in range(args.repeat):
        isolate(directory / f"trial-{index}")
        for mode in ("cold", "warm"):
            sample, current = alignment_trial(source, master, args)
            if reference is None:
                reference = current
            elif current != reference:
                raise RuntimeError("Alignment evidence differs between trials")
            hits = [item["status"] == "cache_hit" for item in sample["sampling"].values()]
            if mode == "cold" and any(hits):
                raise RuntimeError("Cold trial unexpectedly reused cached samples")
            if mode == "warm" and not all(hits):
                raise RuntimeError("Warm trial did not reuse both cached samples")
            runs[mode].append(sample)
    return {"scenario": scenario, "hardware": system_specs(),
            "recipe": {"interval_s": args.interval, "max_samples": args.max_samples,
                       "decoder": args.decoder, "requested_workers": args.workers,
                       "synthetic_duration_s": args.duration if not args.source else None},
            "native_matcher_available": _library() is not None,
            "evidence_identical": True, "runs": runs,
            "summary": {mode: {key: summarize([row[key] for row in rows])
                              for key in ("wall_seconds", "cpu_seconds_including_waited_children")}
                        for mode, rows in runs.items()}}


def gui_suite(args) -> dict:
    # Real Qt window, synthetic empty session. No media, no audio or GPU claims.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    started = time.perf_counter()
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from dubsync.gui.main_window import MainWindow

    app = QApplication([])
    app.setStyle("Fusion")
    window = MainWindow()
    try:
        window.resize(1280, 720)
        window.show()
        app.processEvents()
        startup = time.perf_counter() - started
        pss = pss_mib()
        idle = QEventLoop()
        cpu_start = time.process_time()
        idle_start = time.perf_counter()
        QTimer.singleShot(args.idle_seconds * 1000, idle.quit)
        idle.exec()
        idle_wall = time.perf_counter() - idle_start
        idle_cpu = time.process_time() - cpu_start
        transitions = []
        for _ in range(args.repeat):
            for page in (1, 2, 0):
                before = time.perf_counter()
                window._navigate_to(page)
                app.processEvents()
                transitions.append(time.perf_counter() - before)
        return {"qt_platform": app.platformName(), "viewport": [1280, 720],
                "startup_import_construct_show_seconds": startup,
                "parent_pss_mib_after_show": pss,
                "parent_pss_mib_after_navigation": pss_mib(),
                "idle_wall_seconds": idle_wall,
                "idle_cpu_percent_one_core": 100 * idle_cpu / idle_wall,
                "empty_workspace_navigation_seconds": summarize(transitions)}
    finally:
        window.project_dirty = False
        window.close()
        app.processEvents()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("alignment", "gui"), default="alignment")
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument("master", nargs="?", type=Path)
    parser.add_argument("--scenario", choices=("same", "shifted"), default="same")
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--interval", type=float, default=.5)
    parser.add_argument("--max-samples", type=int, default=1800)
    parser.add_argument("--decoder", choices=("ffmpeg", "auto"), default="ffmpeg")
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    parser.add_argument("--idle-seconds", type=int, default=5)
    args = parser.parse_args(argv)
    if bool(args.source) != bool(args.master) or (args.source and args.suite == "gui"):
        parser.error("Specify both SOURCE MASTER for alignment only")
    if not (30 <= args.duration <= 240 and 1 <= args.repeat <= 8
            and .5 <= args.interval <= 15 and 200 <= args.max_samples <= 5000
            and 1 <= args.idle_seconds <= 30):
        parser.error("Benchmark settings outside supported bounds")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="dubsync-baseline-") as temp:
        directory = Path(temp)
        isolate(directory / "initial")
        result = (alignment_suite(directory, args) if args.suite == "alignment"
                  else gui_suite(args))
        report = {"schema_version": 1, "suite": args.suite, "revision": revision(),
                  "python": platform.python_version(), "platform": platform.system(),
                  "machine": platform.machine(), "repetitions": args.repeat,
                  "result": result}
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
