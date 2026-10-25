from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "installer_wizard.py"
sys.path.insert(0, str(ROOT))
import installer_wizard  # noqa: E402


class InstallerWizardTests(unittest.TestCase):
    def test_cli_help_and_version_do_not_open_gui(self):
        help_result = subprocess.run(
            [sys.executable, str(WIZARD), "--help"], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("grafischer", help_result.stdout.lower())
        version_result = subprocess.run(
            [sys.executable, str(WIZARD), "--version"], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(version_result.returncode, 0, version_result.stderr)
        self.assertIn("1.5.14", version_result.stdout)
        release = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "release_check.py")],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(release.returncode, 0, release.stdout + release.stderr)

    def test_check_json_reports_prerequisites_without_display(self):
        result = subprocess.run(
            [sys.executable, str(WIZARD), "--check", "--json"],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["application"], "DubSync Studio")
        self.assertTrue(report["python"])
        self.assertIn("tkinter", report)
        self.assertIn("ffmpeg", report)
        self.assertTrue(report["install_script"])
        self.assertTrue(report["setup_script"])

    def test_default_destination_uses_saved_managed_installation(self):
        with tempfile.TemporaryDirectory(prefix="dubsync wizard config ") as td:
            home = Path(td)
            config = home / ".config" / "dubsync-studio"
            install = home / "Applications" / "DubSync Studio"
            (install / "dubsync").mkdir(parents=True)
            (install / "dubsync" / "__init__.py").write_text("__version__='1.5.1'\n", encoding="utf-8")
            (install / ".dubsync-install.json").write_text("{}", encoding="utf-8")
            config.mkdir(parents=True)
            (config / "install.json").write_text(json.dumps({"install_dir": str(install)}), encoding="utf-8")
            env = {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
            }
            self.assertEqual(installer_wizard.default_install_dir(env), install)

    def test_normalizes_spaces_and_allows_default_xdg_app_path(self):
        with tempfile.TemporaryDirectory(prefix="dubsync wizard home ") as td:
            home = Path(td)
            target = home / ".local" / "share" / "dubsync-studio" / "app"
            self.assertEqual(installer_wizard.normalize_install_dir(str(target), home), target)
            spaced = home / "Applications" / "DubSync Studio"
            self.assertEqual(installer_wizard.normalize_install_dir(str(spaced), home), spaced)

    def test_rejects_home_and_config_locations(self):
        with tempfile.TemporaryDirectory(prefix="dubsync wizard home ") as td:
            home = Path(td)
            for value in (str(home), str(home / ".config" / "dubsync-studio"), "/"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    installer_wizard.normalize_install_dir(value, home)

    def test_wizard_builds_and_navigates_pages_under_virtual_display_when_available(self):
        import shutil
        xvfb = shutil.which("xvfb-run")
        if not xvfb:
            self.skipTest("xvfb-run fehlt; interaktiver Tk-Smoke-Test benötigt virtuellen X-Server")
        code = r"""
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import filedialog, messagebox
from installer_wizard import InstallerWizard
root = tk.Tk()
root.withdraw()
wizard = InstallerWizard(root, tk, ttk, filedialog, messagebox)
root.update()
assert len(wizard.pages) == 6
for index in (1, 2, 3, 4, 5, 0):
    wizard._show_page(index)
    root.update()
assert wizard.summary.get("1.0", "end").strip()
assert wizard.checks_text.get("1.0", "end").strip()
root.destroy()
print("INSTALLER-GUI-SMOKE-OK")
"""
        result = subprocess.run(
            [xvfb, "-a", sys.executable, "-c", code], cwd=ROOT,
            capture_output=True, text=True, timeout=25,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("INSTALLER-GUI-SMOKE-OK", result.stdout)

    def test_desktop_wizard_and_shell_entrypoint_are_included_in_package(self):
        self.assertTrue((ROOT / "install-wizard.sh").exists())
        self.assertTrue((ROOT / "installer_wizard.py").exists())
        script = (ROOT / "install-wizard.sh").read_text(encoding="utf-8")
        self.assertIn("installer_wizard.py", script)
        self.assertIn("Tkinter", script)
        wizard = WIZARD.read_text(encoding="utf-8")
        for feature in ("Willkommen", "Installationsordner", "Desktop-Verknüpfung", "Systemprüfung", "Fortschrittsprotokoll"):
            self.assertIn(feature, wizard)


if __name__ == "__main__":
    unittest.main(verbosity=2)
