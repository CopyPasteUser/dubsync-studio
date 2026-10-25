from __future__ import annotations
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LinuxInstallationTests(unittest.TestCase):
    def test_all_shell_entrypoints_have_valid_syntax(self):
        for name in ("setup.sh", "run.sh", "install.sh", "install-wizard.sh", "install-desktop.sh", "uninstall.sh"):
            with self.subTest(script=name):
                result = subprocess.run(
                    ["bash", "-n", str(ROOT / name)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_installer_help_is_safe_and_documents_options(self):
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh"), "--help"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--desktop-shortcut", result.stdout)
        self.assertIn("Root-Zugriff", result.stdout)
        self.assertIn("--install-dir", result.stdout)

    def test_uninstaller_help_explains_data_preservation(self):
        result = subprocess.run(
            ["bash", str(ROOT / "uninstall.sh"), "--help"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--purge-data", result.stdout)
        self.assertIn("bleiben Einstellungen", result.stdout)

    def test_installation_is_user_local_and_xdg_based(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("${XDG_DATA_HOME:-$HOME/.local/share}", installer)
        self.assertIn("$HOME/.local/bin", installer)
        self.assertIn("applications", installer)
        self.assertIn("dubsync-studio.desktop", installer)
        self.assertIn("--desktop-shortcut", installer)
        self.assertNotIn("sudo ", installer)

    def test_setup_has_cross_distro_hints_without_auto_installing_system_packages(self):
        setup = (ROOT / "setup.sh").read_text(encoding="utf-8")
        for package_manager in ("apt", "dnf", "pacman", "zypper", "apk"):
            self.assertIn(package_manager, setup)
        self.assertIn("-m venv .venv", setup)
        self.assertNotIn('sudo "$', setup)

    def test_desktop_icon_and_launcher_are_present_in_source_distribution(self):
        self.assertTrue((ROOT / "assets" / "dubsync-studio.svg").is_file())
        self.assertTrue((ROOT / "dubsync" / "assets" / "dubsync-studio.svg").is_file())
        wrapper = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('Type=Application', wrapper)
        self.assertIn('Terminal=false', wrapper)
        self.assertIn('Categories=AudioVideo;Video;Audio;Utility;', wrapper)
        self.assertIn('"Exec=" + quote(launcher)', wrapper)
        self.assertIn('"Icon=dubsync-studio"', wrapper)
        self.assertNotIn('"TryExec=" + quote(launcher)', wrapper)
        self.assertIn('desktop-file-validate "$DESKTOP_FILE"', wrapper)

    def test_desktop_repair_tool_generates_standard_entry_without_broken_tryexec(self):
        repair = (ROOT / "repair-desktop-entry.sh").read_text(encoding="utf-8")
        self.assertIn('"Exec=" + desktop_exec_arg(launcher)', repair)
        self.assertIn('"Icon=dubsync-studio"', repair)
        self.assertNotIn('"TryExec="', repair)
        self.assertIn('desktop-file-validate', repair)
        self.assertIn('kbuildsycoca6', repair)

    def test_desktop_shell_identity_is_set_for_window_grouping(self):
        gui = (ROOT / "dubsync" / "gui" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn('app.setApplicationName("dubsync-studio")', gui)
        self.assertIn('app.setDesktopFileName("dubsync-studio")', gui)


if __name__ == "__main__":
    unittest.main(verbosity=2)
