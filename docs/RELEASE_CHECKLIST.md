# Release checklist

- [ ] Run `./setup.sh` in a clean environment with FFmpeg and FFprobe installed.
- [ ] Run `./release-check.sh --require-gui` on the target OS to require PySide6.
- [ ] Open the GUI and verify project save/open, missing-source warning, settings dialog, job history, log viewer and close confirmation.
- [ ] Open and save a project containing manual track/master overrides, close and reopen it, and verify the overrides remain.
- [ ] Export a short synthetic/owned test episode to MKV; verify audio labels, codec, duration and that sources are unchanged.
- [ ] Verify an interrupted batch is recorded after forced test-process termination and visible as interrupted on next start.
- [ ] Check output disk-space warnings and the existing-output skip path.
- [ ] Test 1366×768 and 1920×1080 displays plus long Windows/Linux file paths.
- [ ] Review logs and project files for private paths before attaching them to an issue.
- [ ] Upgrade an existing JSON alignment cache and verify the resulting SQLite cache remains readable.
- [ ] On a clean user account, run `./install.sh`; verify the source download folder can be removed and the app still launches from the application menu.
- [ ] Verify the generated `.desktop` file with `desktop-file-validate` when available and check app grouping/icon in GNOME, KDE Plasma, Cinnamon or XFCE.
- [ ] Run `./install.sh --desktop-shortcut` and verify the optional desktop icon path on the target environment.
- [ ] Run `~/.local/share/dubsync-studio/app/uninstall.sh`; verify config/cache/state persist. Re-run on a disposable test user with `--purge-data` and verify the explicit purge path.
- [ ] Repeat setup on Debian/Ubuntu/Mint, Fedora, Arch/Manjaro, and openSUSE where possible; document unavailable PySide6/OpenCV wheels and distro-specific Qt/FFmpeg runtime libraries.
- [ ] Keep Alpine/musl and non-mainstream CPU architectures listed as unverified unless a compatible dependency build is provided.
