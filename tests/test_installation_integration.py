from __future__ import annotations
import json
import os
import shutil
import configparser
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UserInstallerIntegrationTests(unittest.TestCase):
    def test_install_creates_portable_user_app_launcher_icon_and_desktop_entry(self):
        # Use a fake venv interpreter so this integration test exercises the
        # installer without network access or installing packages on the host.
        with tempfile.TemporaryDirectory(prefix="dubsync install home ") as temporary:
            home = Path(temporary) / "user home with spaces"
            data = home / ".local" / "share"
            app = data / "dubsync-studio" / "app"
            venv_python = app / ".venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = \"-m\" ] && [ \"${2:-}\" = \"dubsync\" ]; then\n"
                "  if [ \"${3:-}\" = \"--version\" ]; then echo 'DubSync Studio 1.5.10'; fi\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            venv_python.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "XDG_DATA_HOME": str(data),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_STATE_HOME": str(home / ".local" / "state"),
                "PYTHON": "python3",
            })
            result = subprocess.run(
                ["bash", str(ROOT / "install.sh")], cwd=ROOT, env=env,
                capture_output=True, text=True, timeout=90, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            launcher = home / ".local" / "bin" / "dubsync-studio"
            desktop = data / "applications" / "dubsync-studio.desktop"
            icon = data / "icons" / "hicolor" / "scalable" / "apps" / "dubsync-studio.svg"
            self.assertTrue(launcher.is_file())
            self.assertTrue(os.access(launcher, os.X_OK))
            self.assertTrue(desktop.is_file())
            self.assertTrue(icon.is_file())
            self.assertTrue((app / "dubsync" / "__main__.py").is_file())
            desktop_text = desktop.read_text(encoding="utf-8")
            self.assertIn('Type=Application', desktop_text)
            self.assertIn('Terminal=false', desktop_text)
            self.assertIn('Icon=dubsync-studio', desktop_text)
            self.assertIn('StartupWMClass=dubsync-studio', desktop_text)
            self.assertNotIn('TryExec=', desktop_text)
            self.assertIn(str(launcher), desktop_text)
            parser = configparser.ConfigParser(interpolation=None, strict=True)
            parser.optionxform = str
            parser.read_string(desktop_text)
            entry = parser['Desktop Entry']
            self.assertEqual(entry['Type'], 'Application')
            self.assertEqual(entry['Terminal'], 'false')
            self.assertEqual(entry['Icon'], 'dubsync-studio')
            self.assertTrue(entry['Exec'].startswith('"'))
            validator = shutil.which('desktop-file-validate')
            if validator:
                checked = subprocess.run(
                    [validator, str(desktop)], capture_output=True, text=True,
                    timeout=10, check=False,
                )
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

            version = subprocess.run(
                [str(launcher), "--version"], env=env,
                capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(version.returncode, 0, version.stdout + version.stderr)
            self.assertIn("DubSync Studio 1.5.10", version.stdout)

    def test_install_supports_custom_destination_with_spaces_and_uninstalls_only_app_folder(self):
        with tempfile.TemporaryDirectory(prefix="dubsync custom install ") as temporary:
            home = Path(temporary) / "user home with spaces"
            custom = home / "Applications" / "DubSync Studio"
            venv_python = custom / ".venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = \"-m\" ] && [ \"${2:-}\" = \"dubsync\" ]; then\n"
                "  if [ \"${3:-}\" = \"--version\" ]; then echo 'DubSync Studio 1.5.10'; fi\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            venv_python.chmod(0o755)
            env = os.environ.copy()
            data = home / ".local" / "share"
            env.update({
                "HOME": str(home),
                "XDG_DATA_HOME": str(data),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_STATE_HOME": str(home / ".local" / "state"),
                "PYTHON": "python3",
            })
            result = subprocess.run(
                ["bash", str(ROOT / "install.sh"), "--install-dir", str(custom)],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=90, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            marker = json.loads((custom / ".dubsync-install.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(marker["install_dir"]), custom.resolve())
            metadata = json.loads((home / ".config" / "dubsync-studio" / "install.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(metadata["install_dir"]), custom.resolve())
            launcher = home / ".local" / "bin" / "dubsync-studio"
            version = subprocess.run(
                [str(launcher), "--version"], env=env, capture_output=True,
                text=True, timeout=10, check=False,
            )
            self.assertEqual(version.returncode, 0, version.stdout + version.stderr)
            self.assertIn("DubSync Studio 1.5.10", version.stdout)

            uninstall = subprocess.run(
                ["bash", str(custom / "uninstall.sh")], env=env, cwd=ROOT,
                capture_output=True, text=True, timeout=20, check=False,
            )
            self.assertEqual(uninstall.returncode, 0, uninstall.stdout + uninstall.stderr)
            self.assertFalse(custom.exists())
            self.assertTrue(custom.parent.exists(), "parent directory chosen by user must not be deleted")
            self.assertFalse(launcher.exists())
            self.assertFalse((home / ".config" / "dubsync-studio" / "install.json").exists())

    def test_repair_script_repairs_menu_and_existing_desktop_shortcut(self):
        with tempfile.TemporaryDirectory(prefix="dubsync desktop repair ") as temporary:
            home = Path(temporary) / "deck home"
            data = home / ".local" / "share"
            launcher = home / ".local" / "bin" / "dubsync-studio"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(0o755)
            icon_dir = data / "icons" / "hicolor" / "scalable" / "apps"
            icon_dir.mkdir(parents=True)
            (icon_dir / "dubsync-studio.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>\n',
                encoding="utf-8",
            )
            applications = data / "applications"
            applications.mkdir(parents=True)
            menu = applications / "dubsync-studio.desktop"
            menu.write_text('[Desktop Entry]\nTryExec="/broken path"\n', encoding="utf-8")
            desktop_dir = home / "Desktop"
            desktop_dir.mkdir()
            shortcut = desktop_dir / "DubSync Studio.desktop"
            shortcut.write_text('[Desktop Entry]\nTryExec="/broken path"\n', encoding="utf-8")

            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "XDG_DATA_HOME": str(data),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DESKTOP_DIR": str(desktop_dir),
            })
            result = subprocess.run(
                ["bash", str(ROOT / "repair-desktop-entry.sh")],
                env=env, cwd=ROOT, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for fixed in (menu, shortcut):
                text = fixed.read_text(encoding="utf-8")
                self.assertIn('[Desktop Entry]', text)
                self.assertIn('Icon=dubsync-studio', text)
                self.assertIn(str(launcher).replace('%', '%%'), text)
                self.assertNotIn('TryExec=', text)
            self.assertTrue(os.access(shortcut, os.X_OK))
            self.assertTrue(list(applications.glob('dubsync-studio.desktop.bak.*')))
            self.assertTrue(list(desktop_dir.glob('DubSync Studio.desktop.bak.*')))
            validator = shutil.which('desktop-file-validate')
            if validator:
                checked = subprocess.run(
                    [validator, str(menu)], capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_install_refuses_to_empty_a_foreign_nonempty_destination(self):
        with tempfile.TemporaryDirectory(prefix="dubsync foreign target ") as temporary:
            home = Path(temporary) / "home"
            target = home / "Applications" / "Existing Stuff"
            target.mkdir(parents=True)
            note = target / "do-not-delete.txt"
            note.write_text("user content", encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_STATE_HOME": str(home / ".local" / "state"),
            })
            result = subprocess.run(
                ["bash", str(ROOT / "install.sh"), "--install-dir", str(target)],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=15, check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(note.is_file())
            self.assertEqual(note.read_text(encoding="utf-8"), "user content")

    def test_uninstall_preserves_user_data_by_default_and_purge_is_explicit(self):
        with tempfile.TemporaryDirectory(prefix="dubsync uninstall ") as temporary:
            home = Path(temporary) / "home"
            data = home / ".local" / "share"
            app = data / "dubsync-studio" / "app"
            app.mkdir(parents=True)
            config = home / ".config" / "dubsync-studio"
            cache = home / ".cache" / "dubsync-studio"
            state = home / ".local" / "state" / "dubsync-studio"
            for folder in (config, cache, state):
                folder.mkdir(parents=True)
                (folder / "keep.txt").write_text("keep", encoding="utf-8")
            launcher = home / ".local" / "bin" / "dubsync-studio"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            applications = data / "applications"
            applications.mkdir(parents=True)
            (applications / "dubsync-studio.desktop").write_text("[Desktop Entry]\n", encoding="utf-8")
            icons = data / "icons" / "hicolor" / "scalable" / "apps"
            icons.mkdir(parents=True)
            (icons / "dubsync-studio.svg").write_text("<svg/>", encoding="utf-8")

            env = os.environ.copy()
            env.update({
                "HOME": str(home), "XDG_DATA_HOME": str(data),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_STATE_HOME": str(home / ".local" / "state"),
            })
            normal = subprocess.run(
                ["bash", str(ROOT / "uninstall.sh")], env=env,
                capture_output=True, text=True, timeout=15, check=False,
            )
            self.assertEqual(normal.returncode, 0, normal.stdout + normal.stderr)
            self.assertFalse(app.parent.exists())
            self.assertFalse(launcher.exists())
            self.assertTrue((config / "keep.txt").exists())
            self.assertTrue((cache / "keep.txt").exists())
            self.assertTrue((state / "keep.txt").exists())

            # Recreate the installed shell files and verify explicit data purge.
            app.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            applications.mkdir(parents=True, exist_ok=True)
            (applications / "dubsync-studio.desktop").write_text("[Desktop Entry]\n", encoding="utf-8")
            icons.mkdir(parents=True, exist_ok=True)
            (icons / "dubsync-studio.svg").write_text("<svg/>", encoding="utf-8")
            purge = subprocess.run(
                ["bash", str(ROOT / "uninstall.sh"), "--purge-data"], env=env,
                capture_output=True, text=True, timeout=15, check=False,
            )
            self.assertEqual(purge.returncode, 0, purge.stdout + purge.stderr)
            self.assertFalse(config.exists())
            self.assertFalse(cache.exists())
            self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
