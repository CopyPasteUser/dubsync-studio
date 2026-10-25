# Linux-Installation und Desktop-Integration

DubSync Studio verwendet den Freedesktop/XDG-Desktop-Eintrag und eine benutzerlokale Python-Umgebung. Der Installer benötigt keinen Root-Zugriff und verändert keine systemweiten Pakete, Themes, Panel-Einstellungen oder Icons.

## Empfohlene Installation

1. Archiv entpacken und den enthaltenen Projektordner öffnen.
2. Den grafischen Installationsassistenten starten:

   ```bash
   chmod +x install-wizard.sh install.sh setup.sh run.sh uninstall.sh install-desktop.sh
   ./install-wizard.sh
   ```

3. Den Schritten Willkommen, Zielordner, Optionen, Systemprüfung und Installation folgen. Danach im Anwendungsmenü nach **DubSync Studio** suchen.

Der Wizard benutzt Tkinter aus der Python-Standardbibliothek, damit er vor PySide6 starten kann. Falls Tkinter nicht vorhanden ist, installiere das Tk-Paket deiner Distribution oder nutze `./install.sh` als Terminal-Fallback.

Standardmäßig installiert die App nach `${XDG_DATA_HOME:-~/.local/share}/dubsync-studio/app`, legt `~/.local/bin/dubsync-studio`, den Anwendungsmenüeintrag und das SVG-Icon an. Der Zielordner kann im Wizard angepasst werden. Für die Terminalvariante geht das beispielsweise so:

```bash
./install.sh --install-dir "/home/ich/Applications/DubSync Studio"
./install.sh --desktop-shortcut
```

Bei Updates bleibt die bestehende `.venv` erhalten. Der Installer verweigert fremde, nicht leere Ordner und protokolliert den verwalteten Installationspfad in `~/.config/dubsync-studio/install.json`. Der Download-/Entpackordner kann nach erfolgreicher Installation entfernt werden.

## Unterstützte Umgebungen

Der Installer richtet sich an gängige glibc-basierte Linux-Distributionen mit Python 3.10 oder neuer und verfügbaren PyPI-Wheels für PySide6, NumPy und OpenCV, unter anderem:

- Debian, Ubuntu, Linux Mint und Pop!_OS
- Fedora (FFmpeg kann ein zusätzliches Repository erfordern)
- Arch Linux, Manjaro und verwandte Distributionen
- openSUSE Leap/Tumbleweed, abhängig von verfügbaren Medienpaketen

Auch GNOME, KDE Plasma, Cinnamon, XFCE, MATE und andere XDG-kompatible Desktop-Umgebungen können den Anwendungsmenüeintrag verwenden. Es ist kein XFCE-spezifischer Code erforderlich.

Alpine/musl, ungewöhnliche Architekturen oder Systeme ohne passende PySide6-Wheels sind nicht pauschal garantiert. Auf solchen Systemen müssen gegebenenfalls Qt-/Python-Abhängigkeiten über die Distribution installiert oder passend gebaut werden.

## Systemwerkzeuge

Erforderlich sind:

- Python 3.10+ einschließlich `venv`/`ensurepip` und pip
- FFmpeg und FFprobe
- für die Oberfläche die Python-Abhängigkeit PySide6; NumPy und OpenCV werden für die Medienanalyse benötigt

`./setup.sh` installiert Python-Pakete nur in `.venv`. Fehlende Systemwerkzeuge werden angezeigt, einschließlich eines Vorschlags für den erkannten Paketmanager. Es werden **keine** `apt`, `dnf`, `pacman` oder `zypper`-Befehle automatisch mit Root-Rechten gestartet.

Beispiele für manuelle Pakete:

- Debian/Ubuntu/Mint: `sudo apt install python3 python3-venv python3-pip ffmpeg libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0`
- Fedora: `sudo dnf install python3 python3-pip ffmpeg mesa-libGL libxkbcommon xcb-util-cursor` (FFmpeg ggf. aus einem zusätzlichen Repository)
- Arch/Manjaro: `sudo pacman -S python python-pip ffmpeg mesa libxkbcommon xcb-util-cursor`
- openSUSE: `sudo zypper install python3 python3-pip ffmpeg Mesa-libGL1 libxkbcommon-x11-0`

Paketnamen können je nach Release variieren; installiere Systempakete nur, wenn sie wirklich fehlen.

## Portable Nutzung ohne Installation

Für Test oder portable Nutzung kann die App direkt aus dem entpackten Ordner laufen:

```bash
./setup.sh
./run.sh
```

Das erzeugt die `.venv` im Projektordner. Wird der Ordner anschließend verschoben, muss `.venv` neu erstellt werden (virtuelle Umgebungen enthalten absolute Pfade).

## Diagnose

Nach der Installation:

```bash
~/.local/bin/dubsync-studio --doctor
~/.local/bin/dubsync-studio --doctor --json
```

Wenn der Desktop-Start scheitert, zeigt die App nach Möglichkeit eine Benachrichtigung. Das Launcher-Protokoll liegt unter `${XDG_STATE_HOME:-~/.local/state}/dubsync-studio/logs/launcher.log`.

## Deinstallation

Normale Deinstallation der Anwendung, wobei Einstellungen, Cache und Jobhistorie erhalten bleiben:

```bash
~/.local/share/dubsync-studio/app/uninstall.sh
```

Alles einschließlich Benutzerdaten entfernen (bewusste, irreversible Aktion):

```bash
~/.local/share/dubsync-studio/app/uninstall.sh --purge-data
```

Die Benutzerdaten liegen standardmäßig unter `~/.config/dubsync-studio`, `~/.cache/dubsync-studio` und `~/.local/state/dubsync-studio`. Der Installer löscht diese bei normaler Deinstallation nicht.
