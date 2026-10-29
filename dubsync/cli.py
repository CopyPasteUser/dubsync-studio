"""Command-line support for system diagnostics; GUI dependencies are loaded lazily."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from dubsync.core.performance import system_specs, recommended_profile
from typing import Any

VERSION = "2.0.0b2"


def doctor_report() -> dict[str, Any]:
    result: dict[str, Any] = {
        "application_version": VERSION,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "dependencies": {},
        "media_tools": {},
        "hardware": {},
        "paths": {},
        "warnings": [],
    }
    for module in ("PySide6", "numpy", "cv2"):
        result["dependencies"][module] = bool(importlib.util.find_spec(module))
    specs = system_specs()
    result["hardware"] = {
        "logical_cpus_available": specs["logical_cpus"],
        "physical_cores_detected": specs["physical_cores"],
        "ram_gib_visible": round(specs["ram_gib"], 2),
        "gpu_vendors_detected": specs["gpu_vendors"],
        "recommended_analysis_profile": recommended_profile(specs),
    }
    from dubsync.core.ffmpeg_sampling import (ffmpeg_hardware_accels, likely_gpu_accel,
                                              gpu_decode_candidates)
    result["hardware"]["ffmpeg_acceleration_interfaces"] = sorted(ffmpeg_hardware_accels())
    result["hardware"]["likely_gpu_decoder"] = likely_gpu_accel()
    result["hardware"]["auto_gpu_candidates"] = [candidate.key for candidate in gpu_decode_candidates()]
    from dubsync.core.ffmpeg_sampling import vaapi_render_device
    result["hardware"]["linux_vaapi_nodes"] = {
        "amd": vaapi_render_device("amd"), "intel": vaapi_render_device("intel")}
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        info = {"available": bool(path), "path": path or ""}
        if path:
            try:
                proc = subprocess.run([path, "-version"], capture_output=True,
                                      text=True, timeout=5, check=False)
                info["version"] = (proc.stdout or proc.stderr).splitlines()[0][:300]
                info["returncode"] = proc.returncode
            except (OSError, subprocess.TimeoutExpired) as exc:
                info["version_error"] = str(exc)
        result["media_tools"][tool] = info
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dubsync-studio"
    cache_home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "dubsync-studio"
    state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "dubsync-studio"
    result["paths"] = {"config": str(config_home), "cache": str(cache_home),
                       "state": str(state_home), "log": str(state_home / "logs" / "dubsync.log")}
    if not result["dependencies"]["PySide6"]:
        result["warnings"].append("PySide6 fehlt: Die Desktop-Oberfläche kann nicht gestartet werden.")
    if not result["dependencies"]["numpy"] or not result["dependencies"]["cv2"]:
        result["warnings"].append("NumPy oder OpenCV fehlt: Synchronisationsanalyse ist nicht verfügbar.")
    for tool, info in result["media_tools"].items():
        if not info["available"]:
            result["warnings"].append(f"{tool} fehlt: Medienanalyse und Export sind eingeschränkt.")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dubsync-studio", description="Mehrsprachige Videos analysieren und zusammenführen.")
    parser.add_argument("--version", action="version", version=f"DubSync Studio {VERSION}")
    parser.add_argument("--doctor", action="store_true", help="System- und Abhängigkeitsdiagnose anzeigen.")
    parser.add_argument("--latest-alignment", action="store_true", help="Letzten Bildabgleich-Diagnosebericht anzeigen (auch bei Fehlern).")
    parser.add_argument("--list-alignments", action="store_true", help="Bis zu 40 Bildabgleichberichte auflisten.")
    parser.add_argument("--latest-batch", action="store_true", help="Gesamtprotokoll des neuesten Stapels anzeigen (ALLE Folgen).")
    parser.add_argument("--list-batches", action="store_true", help="Gesamtprotokolle der letzten Stapel anzeigen.")
    parser.add_argument("--benchmark-decode", metavar="VIDEO", help="CPU/GPU-Dekodierleistung für ein lokales Video messen (nur lesend).")
    parser.add_argument("--profile-alignment", nargs=2, metavar=("QUELLE", "MASTER"),
                        help="Vollständigen Bildabgleich und Laufzeiten für zwei Videos prüfen (kein Export).")
    parser.add_argument("--profile-workers", choices=(1, 2), type=int, default=1,
                        help="Video-Decoder für --profile-alignment: 1 oder 2 (NAS bleibt seriell).")
    parser.add_argument("--profile-decoder", choices=("auto", "none", "ffmpeg", "cuda", "vaapi",
                                                      "amd-vaapi", "intel-vaapi", "d3d11"), default="auto",
                        help="Video-Decoding für --profile-alignment, Standard auto.")
    parser.add_argument("--benchmark-seconds", type=float, default=8.0,
                        help="Länge des Benchmark-Ausschnitts in Sekunden, Standard: 8.")
    parser.add_argument("--benchmark-gpu", choices=("auto", "all", "none", "amd-vaapi", "intel-vaapi", "vaapi", "cuda", "d3d11"),
                        default="auto", help="GPU-Backend im Decoder-Benchmark gezielt wählen.")
    parser.add_argument("--json", action="store_true", help="Diagnose maschinenlesbar als JSON ausgeben (mit --doctor).")
    args = parser.parse_args(argv)
    if args.json and not (args.doctor or args.latest_alignment or args.list_alignments or args.latest_batch or args.list_batches or args.benchmark_decode or args.profile_alignment):
        parser.error("--json erfordert eine Diagnoseoption.")
    if sum(bool(value) for value in (args.doctor, args.latest_alignment, args.list_alignments, args.latest_batch, args.list_batches, args.benchmark_decode, args.profile_alignment)) > 1:
        parser.error("Bitte nur eine Diagnosefunktion gleichzeitig auswählen.")
    if args.profile_alignment:
        from dubsync.core.alignment import align_videos
        from dubsync.core.alignment_diagnostics import summarize_diagnostic
        source, master = (Path(value).expanduser().resolve() for value in args.profile_alignment)
        if not source.is_file() or not master.is_file():
            print("Mindestens eine Videodatei fehlt oder ist nicht lesbar.", file=sys.stderr)
            return 2
        captured: list[dict[str, Any]] = []
        try:
            align_videos(str(source), str(master), interval=0.75, max_samples=1200,
                         hardware_accel=args.profile_decoder,
                         sampling_workers=args.profile_workers,
                         frame_cache_enabled=False,
                         diagnostic_sink=captured.append)
        except (OSError, ValueError, RuntimeError) as exc:
            if not args.json:
                print(f"Analyse fehlgeschlagen: {exc}", file=sys.stderr)
            if args.json:
                print(json.dumps(captured[-1] if captured else {"error": str(exc)},
                                 ensure_ascii=False, indent=2))
            return 1
        if not captured:
            print("Diagnose wurde nicht erstellt.", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(captured[-1], ensure_ascii=False, indent=2))
        else:
            print(summarize_diagnostic(captured[-1]))
        return 0
    if args.benchmark_decode:
        from dubsync.core.alignment import perceptual_hash
        from dubsync.core.ffmpeg_sampling import (benchmark_decoders, benchmark_decoder_candidates,
                                                  gpu_decode_candidates, vaapi_render_device)
        from dubsync.core.probe import probe_media
        source = Path(args.benchmark_decode).expanduser().resolve()
        if not source.is_file():
            print(f"Videodatei nicht gefunden: {source}", file=sys.stderr)
            return 2
        if not (0.5 <= args.benchmark_seconds <= 120):
            parser.error("--benchmark-seconds muss zwischen 0,5 und 120 liegen.")
        try:
            info = probe_media(str(source))
            if args.benchmark_gpu in ("auto", "all"):
                # The CLI mirrors the real automatic selection: one CPU
                # reference against all accessible local GPU candidates.
                outcome = benchmark_decoder_candidates(
                    str(source), info.duration, 0.75, perceptual_hash,
                    candidates=gpu_decode_candidates(), seconds=args.benchmark_seconds)
            else:
                gpu = (None if args.benchmark_gpu == "none" else
                       "d3d11va" if args.benchmark_gpu == "d3d11" else
                       "vaapi" if args.benchmark_gpu in ("amd-vaapi", "intel-vaapi") else args.benchmark_gpu)
                device = (vaapi_render_device("amd" if args.benchmark_gpu == "amd-vaapi" else
                                              "intel" if args.benchmark_gpu == "intel-vaapi" else None)
                          if gpu == "vaapi" else None)
                if args.benchmark_gpu in ("amd-vaapi", "intel-vaapi") and not device:
                    print("Kein zugreifbares VAAPI-Rendergerät des gewählten Herstellers gefunden.", file=sys.stderr)
                    return 2
                outcome = benchmark_decoders(str(source), info.duration, 0.75,
                                             perceptual_hash, gpu=gpu, gpu_device=device,
                                             seconds=args.benchmark_seconds)
        except (ValueError, OSError, RuntimeError) as exc:
            print(f"Decoder-Benchmark fehlgeschlagen: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"file": str(source), "measurements": outcome},
                             ensure_ascii=False, indent=2))
        else:
            print(f"Decoder-Benchmark: {source.name} ({args.benchmark_seconds:g} s Ausschnitt)")
            for name, values in outcome.items():
                if "error" in values:
                    print(f"  {name.upper()}: nicht verfügbar – {values['error']}")
                else:
                    print(f"  {name.upper()}: {values['frames']} Samples in "
                          f"{values['seconds']:.3f} s · {values['samples_per_second']} Samples/s")
            if len(outcome) < 2:
                print("Keine betriebsfähige GPU erkannt; nur CPU gemessen.")
            print("GPU-Anforderung bedeutet nicht automatisch GPU-Ausführung oder höhere Geschwindigkeit.")
        return 0 if outcome.get("cpu", {}).get("frames") else 1
    if args.latest_batch or args.list_batches:
        from dubsync.core.batch_diagnostics import recent_batch_diagnostics, summarize_batch
        paths = recent_batch_diagnostics()
        if args.list_batches:
            rows = []
            for path in paths:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    rows.append({"file": str(path), "run_id": data.get("run_id"),
                                 "started_utc": data.get("started_utc"),
                                 "status": data.get("status"), "summary": data.get("summary")})
                except (OSError, ValueError, TypeError):
                    continue
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                if not rows:
                    print("Noch keine Stapel-Gesamtprotokolle vorhanden.")
                for item in rows:
                    print(f"{item['started_utc']} | {item['status']} | {item['summary']}\n  {item['file']}")
            return 0
        if not paths:
            print("Noch kein Stapel-Gesamtprotokoll vorhanden.", file=sys.stderr)
            return 1
        try:
            data = json.loads(paths[0].read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            print(f"Stapelbericht nicht lesbar: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"Stapelbericht: {paths[0]}\n")
            print(summarize_batch(data))
            print("\nMit --latest-batch --json den vollständigen Bericht für ALLE Episoden kopieren.")
        return 0
    if args.list_alignments:
        from dubsync.core.alignment_diagnostics import recent_diagnostics
        items = []
        for path in recent_diagnostics():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items.append({"file": str(path), "started_utc": data.get("started_utc"),
                          "source_filename": data.get("files", {}).get("source", {}).get("filename"),
                          "status": data.get("decision", {}).get("status"),
                          "method": data.get("decision", {}).get("method")})
        if args.json:
            print(json.dumps(items, indent=2, ensure_ascii=False))
        else:
            if not items:
                print("Keine Bildabgleich-Diagnosen vorhanden.")
            for item in items:
                print(f"{item['started_utc']} | {item['source_filename']} | {item['status']} | {item['method']}\n  {item['file']}")
        return 0
    if args.latest_alignment:
        from dubsync.core.alignment_diagnostics import latest_diagnostic
        path = latest_diagnostic()
        if path is None:
            print("Noch kein Bildabgleich-Diagnosebericht vorhanden. Bitte eine frische Analyse starten.", file=sys.stderr)
            return 1
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"Diagnosebericht ist nicht lesbar: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
        else:
            from dubsync.core.alignment_diagnostics import summarize_diagnostic
            print(f"Bericht: {path}\n")
            print(summarize_diagnostic(report))
            print("\nMit --latest-alignment --json den vollständigen Bericht anzeigen.")
        return 0
    if args.doctor:
        report = doctor_report()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"DubSync Studio {VERSION}")
            print(f"System: {report['platform']}")
            print(f"Python: {report['python']}")
            print("\nPython-Abhängigkeiten:")
            for name, available in report["dependencies"].items():
                print(f"  {'OK' if available else 'FEHLT'}  {name}")
            print("\nLeistungsempfehlung:")
            hardware = report["hardware"]
            print(f"  CPU: {hardware['physical_cores_detected']} physische Kerne / {hardware['logical_cpus_available']} nutzbare Threads")
            print(f"  Sichtbarer RAM: {hardware['ram_gib_visible']:.1f} GiB")
            print(f"  Erkanntes GPU-Vendor: {', '.join(hardware['gpu_vendors_detected']) or 'nicht erkannt'}")
            print(f"  Empfohlenes Analyseprofil: {hardware['recommended_analysis_profile']}")
            print("\nMedienwerkzeuge:")
            for name, info in report["media_tools"].items():
                print(f"  {'OK' if info['available'] else 'FEHLT'}  {name}: {info.get('version', info.get('path', 'nicht gefunden'))}")
            print("\nBenutzerpfade:")
            for name, path in report["paths"].items():
                print(f"  {name}: {path}")
            for warning in report["warnings"]:
                print("WARNUNG:", warning)
            if not report["warnings"]:
                print("Alle erforderlichen Abhängigkeiten wurden gefunden.")
        required_ok = all(report["dependencies"].values()) and all(
            data["available"] for data in report["media_tools"].values()
        )
        return 0 if required_ok else 1
    if importlib.util.find_spec("PySide6") is None:
        print(
            "Die Desktop-Oberfläche benötigt PySide6. Führe zuerst ./setup.sh aus; "
            "für eine Diagnose ohne GUI verwende ./run.sh --doctor.",
            file=sys.stderr,
        )
        return 2
    from dubsync.gui.main_window import main as gui_main
    return gui_main()
