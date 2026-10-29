from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import subprocess
import sys
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility; installed via tomli marker.
    import tomli as tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-gui", type=int, default=0)
    args = parser.parse_args()
    errors = []
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    init_text = (ROOT / "dubsync" / "__init__.py").read_text(encoding="utf-8")
    cli_text = (ROOT / "dubsync" / "cli.py").read_text(encoding="utf-8")
    gui_text = (ROOT / "dubsync" / "gui" / "main_window.py").read_text(encoding="utf-8")
    installer_text = (ROOT / "installer_wizard.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name, text, pattern in (
        ("package", init_text, r'__version__\s*=\s*[\'"]([^\'"]+)[\'"]'),
        ("CLI", cli_text, r'VERSION\s*=\s*[\'"]([^\'"]+)[\'"]'),
        ("GUI", gui_text, r'APP_VERSION\s*=\s*[\'"]([^\'"]+)[\'"]'),
        ("Installer GUI", installer_text, r'APP_VERSION\s*=\s*[\'\"]([^\'\"]+)[\'\"]'),
    ):
        match = re.search(pattern, text)
        if not match or match.group(1) != version:
            errors.append(f"Version metadata mismatch: {name} (expected {version})")
    if not re.search(rf"^# DubSync Studio {re.escape(version)}$", readme, re.MULTILINE):
        errors.append("README header does not match package version")
    expected_status = ("Development Status :: 4 - Beta" if re.search(r"b\d+$", version)
                       else "Development Status :: 5 - Production/Stable")
    if expected_status not in pyproject["project"].get("classifiers", []):
        errors.append(f"Package status does not match the version: expected {expected_status}")
    for path in [*(ROOT / "dubsync").rglob("*.py"), ROOT / "installer_wizard.py"]:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"Syntax error in {path.relative_to(ROOT)}: {exc}")
    for tool in ("ffmpeg", "ffprobe"):
        found = subprocess.run(["bash", "-lc", f"command -v {tool}"], capture_output=True, text=True)
        if found.returncode != 0:
            errors.append(f"Required media tool is missing: {tool}")
    has_gui = importlib.util.find_spec("PySide6") is not None
    if not has_gui:
        message = "PySide6 missing in current interpreter; GUI smoke test cannot run here."
        if args.require_gui:
            errors.append(message)
        else:
            print("WARNING:", message)
    else:
        print("OK: PySide6 installed; GUI dependency found.")
    print(f"Release metadata: {version}")
    if errors:
        for error in errors:
            print("ERROR:", error, file=sys.stderr)
        return 1
    print("Release source/metadata checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
