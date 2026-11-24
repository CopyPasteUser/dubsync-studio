#!/usr/bin/env python3
"""Offline, revision-pinned integration acceptance. Never installs or deploys.

Run from a clean Git checkout with the project test dependencies, FFmpeg,
Cargo (cached toolchain/dependencies) and xvfb-run already available.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "c667c90772d1f5357928cbba7af7dd8c73f24c2b"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, timeout=30).strip()


def preflight() -> list[str]:
    errors = []
    for tool in ("git", "ffmpeg", "ffprobe", "cargo", "xvfb-run"):
        if shutil.which(tool) is None:
            errors.append(f"Missing executable: {tool}")
    for module in ("PySide6", "numpy", "cv2", "pytest", "pytestqt", "tkinter", "setuptools", "wheel"):
        if importlib.util.find_spec(module) is None:
            errors.append(f"Missing Python dependency: {module}")
    return errors


def write_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def isolated_env(root: Path, state: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(PYTHONPATH=str(root), PYTHONUNBUFFERED="1", QT_QPA_PLATFORM="offscreen",
               CARGO_NET_OFFLINE="true", PIP_NO_INDEX="1", DUBSYNC_NATIVE="1")
    for key in ("CONFIG", "CACHE", "STATE", "DATA"):
        private = state / key.lower()
        private.mkdir(parents=True, exist_ok=True)
        env[f"XDG_{key}_HOME"] = str(private)
    # Do not redirect the Rust toolchain home; it is already installed locally.
    return env


class StepFailed(RuntimeError):
    pass


class Acceptance:
    def __init__(self, output: Path):
        self.output = output
        self.report = {"schema_version": 1, "status": "running", "steps": [],
                       "physical_qa": "pending", "release_authorized": False}
        self.save()

    def save(self):
        write_json(self.output / "manifest.json", self.report)

    def step(self, name: str, args: list[str], cwd: Path, *, timeout: int = 1200) -> Path:
        out = self.output / f"{name}.stdout"
        err = self.output / f"{name}.stderr"
        state = self.output / "state" / name
        started = time.monotonic()
        row = {"name": name, "status": "running", "stdout": out.name, "stderr": err.name}
        self.report["steps"].append(row)
        self.save()
        print(f"== {name} ==", flush=True)
        try:
            with out.open("wb") as stdout, err.open("wb") as stderr:
                proc = subprocess.Popen(args, cwd=cwd, env=isolated_env(cwd, state),
                                        stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                        start_new_session=True)
                try:
                    code = proc.wait(timeout=timeout)
                except BaseException:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    raise
            row["returncode"] = code
            if code != 0:
                raise StepFailed(f"{name} exited with {code}; inspect {err.name} and {out.name}")
            row["status"] = "passed"
            return out
        except BaseException as exc:
            row["status"] = "failed"
            row["error"] = str(exc)
            raise
        finally:
            row["seconds"] = round(time.monotonic() - started, 3)
            self.save()


@contextmanager
def worktree(repo: Path, revision: str, directory: Path):
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(directory), revision],
                   check=True, timeout=60, stdin=subprocess.DEVNULL)
    try:
        yield directory
    finally:
        # Only remove the private detached checkout created by this context.
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(directory)],
                       check=True, timeout=60, stdin=subprocess.DEVNULL)


def junit_summary(path: Path) -> dict:
    cases = list(ET.parse(path).iter("testcase"))
    skipped = [{"name": case.get("classname", "") + "." + case.get("name", ""),
                "reason": case.find("skipped").get("message", "")}
               for case in cases if case.find("skipped") is not None]
    return {"total": len(cases), "skipped": skipped,
            "failed": sum(case.find("failure") is not None or case.find("error") is not None for case in cases)}


def compare_alignment(before: dict, after: dict) -> dict:
    a, b = before["result"], after["result"]
    if (a["recipe"] != b["recipe"] or a["scenario"] != b["scenario"] or
            a["native_matcher_available"] != b["native_matcher_available"]):
        raise StepFailed("Incompatible alignment benchmark recipes")
    if not (a["evidence_identical"] and b["evidence_identical"] and
            a["evidence_sha256"] == b["evidence_sha256"]):
        raise StepFailed("Alignment evidence differs across revisions")
    return {mode: {"before_seconds": a["summary"][mode]["wall_seconds"]["median"],
                   "after_seconds": b["summary"][mode]["wall_seconds"]["median"]}
            for mode in ("cold", "warm")}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(accept: Acceptance, baseline: Path, candidate: Path, repeats: int):
    python = sys.executable
    output = accept.output
    for label, root in (("baseline", baseline), ("candidate", candidate)):
        accept.step(f"{label}-native-build", ["bash", "build-native.sh"], root)
        accept.step(f"{label}-native-load", [python, "-c", "from dubsync.core.native_match import _library; assert _library() is not None"], root)
    accept.step("candidate-native-tests", ["cargo", "test", "--offline", "--locked", "--manifest-path", "native/Cargo.toml"], candidate)
    accept.step("candidate-source-check", [python, "scripts/release_check.py", "--require-gui", "1"], candidate)
    accept.step("candidate-full-pytest", ["xvfb-run", "-a", python, "-m", "pytest", "-ra", "tests",
                                       f"--junitxml={output / 'pytest.xml'}"], candidate, timeout=3600)
    summary = junit_summary(output / "pytest.xml")
    if not summary["total"] or summary["failed"]:
        raise StepFailed("No complete successful test inventory")
    accept.report["test_inventory"] = summary
    accept.save()
    wheels = output / "wheels"
    wheels.mkdir()
    accept.step("candidate-wheel-build", [python, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-index", "-w", str(wheels), "."], candidate)
    wheel_files = sorted(wheels.glob("*.whl"))
    if len(wheel_files) != 1:
        raise StepFailed("Expected exactly one built wheel")
    accept.step("candidate-wheel-check", [python, "scripts/verify_wheel.py", str(wheel_files[0])], candidate)
    # Same harness on both revisions; the baseline checkout is disposable.
    shutil.copyfile(candidate / "scripts/performance_baseline.py", baseline / "scripts/performance_baseline.py")
    comparisons = {}
    for scenario in ("same", "shifted"):
        reports = []
        for label, root in (("baseline", baseline), ("candidate", candidate)):
            file = accept.step(f"{label}-alignment-{scenario}", [python, "scripts/performance_baseline.py", "--scenario", scenario, "--repeat", str(repeats)], root)
            reports.append(read_json(file))
        comparisons[scenario] = compare_alignment(*reports)
    shutil.copyfile(candidate / "scripts/benchmark_export.py", baseline / "scripts/benchmark_export.py")
    exports = []
    for label, root in (("baseline", baseline), ("candidate", candidate)):
        file = accept.step(f"{label}-export", [python, "scripts/benchmark_export.py", "--repeat", str(repeats)], root)
        exports.append(read_json(file))
    if (not all(r["inputs_unchanged"] for r in exports) or
            exports[0]["evidence"] != exports[1]["evidence"]):
        raise StepFailed("Decoded audio or copied video evidence differs across revisions")
    accept.report["export_comparison"] = {"before_seconds": exports[0]["median_seconds"],
                                          "after_seconds": exports[1]["median_seconds"]}
    gui = {}
    for label, root in (("baseline", baseline), ("candidate", candidate)):
        samples = []
        for i in range(repeats):
            file = accept.step(f"{label}-gui-{i+1}", [python, "scripts/performance_baseline.py", "--suite", "gui", "--repeat", str(repeats)], root)
            samples.append(read_json(file)["result"])
        gui[label] = {key: statistics.median([sample[key] for sample in samples])
                      for key in ("startup_import_construct_show_seconds", "idle_cpu_percent_one_core")}
        gui[label]["preprobed_episode_render_seconds"] = statistics.median(
            [sample["preprobed_episode_render_seconds"]["median"] for sample in samples])
        pss = [sample["parent_pss_mib_after_show"] for sample in samples]
        gui[label]["parent_pss_mib_after_show"] = statistics.median(pss) if all(v is not None for v in pss) else None
    for appearance in ("light", "dark"):
        accept.step(f"candidate-screenshots-{appearance}", [python, "scripts/capture_studio_2.py", "--appearance", appearance, "--output-dir", str(output / "screenshots" / appearance)], candidate)
    accept.report["alignment_comparison"] = comparisons
    accept.report["gui_comparison"] = gui
    accept.report["status"] = "local_checks_passed_review_required"
    # Hardware, screenshot review, performance targets and reported skips are
    # intentionally not silently converted into a production release approval.
    accept.save()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--repeat", type=int, choices=range(2, 6), default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve() if args.output else Path(tempfile.mkdtemp(prefix="dubsync-4.0-evidence-"))
    if output.is_relative_to(ROOT):
        parser.error("Evidence directory must be outside the repository")
    if args.output:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
    accept = Acceptance(output)
    print(f"Evidence directory: {output}", flush=True)
    try:
        errors = preflight()
        if errors:
            raise StepFailed("; ".join(errors))
        if git(ROOT, "status", "--porcelain"):
            raise StepFailed("Use a clean checkout; no user files will be changed")
        candidate_sha = git(ROOT, "rev-parse", "HEAD")
        baseline_sha = git(ROOT, "rev-parse", "--verify", "--end-of-options", args.baseline + "^{commit}")
        accept.report.update(candidate=candidate_sha, baseline=baseline_sha)
        accept.save()
        with tempfile.TemporaryDirectory(prefix="dubsync-4.0-worktrees-") as temp:
            parent = Path(temp)
            with worktree(ROOT, baseline_sha, parent / "baseline") as baseline, worktree(ROOT, candidate_sha, parent / "candidate") as candidate:
                run(accept, baseline, candidate, args.repeat)
    except (Exception, KeyboardInterrupt) as exc:
        accept.report.update(status="blocked_or_failed", error=str(exc))
        accept.save()
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print("Local checks passed; review raw evidence, skips, screenshots and physical hardware gates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
