#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PYTHON="${PYTHON:-python3}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
APP_HOME="$DATA_HOME/dubsync-studio"
DEFAULT_APP_DIR="$APP_HOME/app"
APP_DIR="$DEFAULT_APP_DIR"
INSTALL_DIR_ARG=""
CUSTOM_INSTALL=0
BIN_DIR="$HOME/.local/bin"
APPLICATIONS_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
LAUNCHER="$BIN_DIR/dubsync-studio"
DESKTOP_FILE="$APPLICATIONS_DIR/dubsync-studio.desktop"
ICON_FILE="$ICON_DIR/dubsync-studio.svg"
INSTALL_META="$CONFIG_HOME/dubsync-studio/install.json"
MAKE_DESKTOP_SHORTCUT=0

while (($#)); do
  case "$1" in
    --install-dir)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "--install-dir benötigt einen Pfad." >&2
        exit 2
      fi
      INSTALL_DIR_ARG="$2"
      CUSTOM_INSTALL=1
      shift 2
      ;;
    --desktop-shortcut) MAKE_DESKTOP_SHORTCUT=1; shift ;;
    --no-desktop-shortcut) MAKE_DESKTOP_SHORTCUT=0; shift ;;
    --help|-h)
      cat <<'HELP'
DubSync Studio – benutzerlokale Linux-Installation

Verwendung:
  ./install.sh
  ./install.sh --install-dir "/home/ich/Applications/DubSync Studio"
  ./install.sh --desktop-shortcut

Optionen:
  --install-dir PFAD       Benutzerdefinierter Programmordner (absoluter Pfad empfohlen)
  --desktop-shortcut       Zusätzlich eine Desktop-Verknüpfung anlegen
  --no-desktop-shortcut    Keine Desktop-Verknüpfung anlegen
  --help                   Diese Hilfe anzeigen

Der Anwendungsmenüeintrag wird immer erstellt. Es werden keine Systempakete
installiert, keine Themes verändert und kein Root-Zugriff benötigt.
HELP
      exit 0
      ;;
    *) echo "Unbekannte Option: $1 (siehe --help)" >&2; exit 2 ;;
  esac
done

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 wurde nicht gefunden (PYTHON=$PYTHON). Bitte Python 3.10+ installieren." >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "DubSync Studio benötigt Python 3.10 oder neuer. Gefunden: $("$PYTHON" --version 2>&1)" >&2
  exit 1
fi

for required in setup.sh run.sh pyproject.toml dubsync/__main__.py assets/dubsync-studio.svg install-wizard.sh installer_wizard.py; do
  if [[ ! -e "$SOURCE_DIR/$required" ]]; then
    echo "Installationsquelle unvollständig: $SOURCE_DIR/$required fehlt." >&2
    exit 1
  fi
done

# Bei einer regulären Aktualisierung dieselbe verwaltete Installationsstelle verwenden.
if [[ "$CUSTOM_INSTALL" -eq 1 ]]; then
  APP_DIR="$INSTALL_DIR_ARG"
elif [[ -r "$INSTALL_META" ]]; then
  EXISTING_DIR=$("$PYTHON" - "$INSTALL_META" 2>/dev/null <<'PY' || true
import json, sys
from pathlib import Path
try:
    doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    candidate = Path(str(doc.get("install_dir", ""))).expanduser()
    if candidate.is_absolute() and (candidate / ".dubsync-install.json").is_file() and (candidate / "dubsync" / "__init__.py").is_file():
        print(candidate.resolve())
except Exception:
    pass
PY
)
  if [[ -n "$EXISTING_DIR" ]]; then APP_DIR="$EXISTING_DIR"; fi
fi

# Normalize path and reject unsafe/unmanaged destinations before copying anything.
APP_DIR=$("$PYTHON" - "$APP_DIR" "$SOURCE_DIR" "$HOME" "$CONFIG_HOME" "$CACHE_HOME" "$STATE_HOME" "$DATA_HOME" <<'PY'
from pathlib import Path
import sys
raw, source_raw, home_raw, config_raw, cache_raw, state_raw, data_raw = sys.argv[1:]
target = Path(raw).expanduser().resolve(strict=False)
source = Path(source_raw).resolve()
home = Path(home_raw).resolve()
data_home = Path(data_raw).expanduser().resolve(strict=False)
protected_roots = [Path(x).expanduser().resolve(strict=False) for x in (config_raw, cache_raw, state_raw)]
protected_roots += [data_home / "applications", data_home / "icons"]
protected_exact = {Path("/"), home, data_home, home / ".local", home / ".local" / "bin", data_home / "applications", data_home / "icons"}
if target in protected_exact or target == Path.home():
    raise SystemExit("Unsicherer Installationspfad: bitte einen eigenen App-Ordner auswählen.")
if any(root == target or root in target.parents for root in protected_roots):
    raise SystemExit("Installationsziel darf nicht innerhalb von Konfiguration, Cache oder Statusdaten liegen.")
if source != target and (source in target.parents or target in source.parents):
    raise SystemExit("Installationsordner und Paketquellordner dürfen nicht ineinander verschachtelt sein.")
if source == target and not (target / ".dubsync-install.json").is_file():
    raise SystemExit("Bitte DubSync Studio in einen separaten Ordner installieren, nicht in den entpackten Paketordner.")
if target.exists() and any(target.iterdir()):
    managed = (target / ".dubsync-install.json").is_file() or ((target / "pyproject.toml").is_file() and (target / "dubsync" / "__init__.py").is_file())
    only_venv = all(item.name == ".venv" for item in target.iterdir())
    if not managed and not only_venv:
        raise SystemExit(f"Installationsziel enthält fremde Dateien und wird nicht geleert: {target}")
print(target)
PY
)

for required in setup.sh run.sh pyproject.toml dubsync/__main__.py assets/dubsync-studio.svg install-wizard.sh installer_wizard.py; do
  if [[ ! -e "$SOURCE_DIR/$required" ]]; then
    echo "Installationsquelle unvollständig: $SOURCE_DIR/$required fehlt." >&2
    exit 1
  fi
done

mkdir -p "$(dirname "$APP_DIR")" "$BIN_DIR" "$APPLICATIONS_DIR" "$ICON_DIR"

# Preserve .venv on updates. Only remove other files from an identified app folder.
"$PYTHON" - "$SOURCE_DIR" "$APP_DIR" <<'PY'
from pathlib import Path
import shutil, sys
source = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
if source == target:
    print("Quelle ist bereits die verwaltete Installationsstelle; kopieren übersprungen.")
    raise SystemExit(0)
if source in target.parents or target in source.parents:
    raise SystemExit("Quelle und Installationsziel dürfen nicht ineinander verschachtelt sein.")
target.mkdir(parents=True, exist_ok=True)
children = list(target.iterdir())
managed = (target / ".dubsync-install.json").is_file() or ((target / "pyproject.toml").is_file() and (target / "dubsync" / "__init__.py").is_file())
only_venv = all(child.name == ".venv" for child in children)
if children and not managed and not only_venv:
    raise SystemExit(f"Ziel enthält fremde Dateien; es wird nicht geleert: {target}")
for child in children:
    if child.name == ".venv":
        continue
    if child.is_dir() and not child.is_symlink():
        shutil.rmtree(child)
    else:
        child.unlink()
ignore_names = {".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist", ".mypy_cache"}
def ignore(_directory, names):
    return {name for name in names if name in ignore_names or name.endswith((".pyc", ".pyo", ".mkv", ".mp4", ".wav", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm")) or name.startswith((".backup", ".finish-backup")) or ".bak." in name or name.endswith(".zip")}
shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)
print("Anwendung kopiert nach:", target)
PY

chmod +x "$APP_DIR/setup.sh" "$APP_DIR/run.sh" "$APP_DIR/uninstall.sh"
export DUBSYNC_INSTALL_DIR="$APP_DIR"

# Register the target before dependency installation so a failed/incomplete setup
# can be retried or safely uninstalled; a successful run upgrades status to installed.
write_install_record() {
  local install_status="$1"
  "$PYTHON" - "$APP_DIR" "$INSTALL_META" "$install_status" <<'PY'
import json, os, re, sys, tempfile
from pathlib import Path
app = Path(sys.argv[1]).resolve()
metadata = Path(sys.argv[2]).expanduser().resolve()
status = sys.argv[3]
text = (app / "pyproject.toml").read_text(encoding="utf-8")
match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
version = match.group(1) if match else "unknown"
record = {"managed_by": "dubsync-studio-installer", "install_dir": str(app), "version": version, "status": status}

def atomic_json(destination: Path, payload: dict):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".dubsync-install-", dir=destination.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, destination)
    except Exception:
        try: os.unlink(temporary)
        except OSError: pass
        raise

atomic_json(app / ".dubsync-install.json", record)
atomic_json(metadata, record)
print(f"Installationspfad registriert: {app} (Version {version}; Status {status})")
PY
}

write_install_record "installing"
"$APP_DIR/setup.sh"
write_install_record "installed"

# A stable wrapper continues to work even if the original download folder is removed.
"$PYTHON" - "$APP_DIR" "$LAUNCHER" <<'PY'
from pathlib import Path
import shlex, sys
app = str(Path(sys.argv[1]).resolve())
launcher = Path(sys.argv[2])
python = str(Path(app) / ".venv" / "bin" / "python")
qapp, qpython = shlex.quote(app), shlex.quote(python)
content = f'''#!/usr/bin/env bash
set -u
APP_DIR={qapp}
PYTHON={qpython}
LOG_DIR="${{XDG_STATE_HOME:-$HOME/.local/state}}/dubsync-studio/logs"
if [[ ! -x "$PYTHON" ]]; then
  MESSAGE="DubSync Studio ist nicht vollständig installiert. Bitte installiere es erneut mit install.sh."
  if command -v notify-send >/dev/null 2>&1; then notify-send "DubSync Studio" "$MESSAGE"; fi
  echo "$MESSAGE" >&2
  exit 1
fi
cd "$APP_DIR" || exit 1
if [[ "$#" -eq 0 ]]; then
  mkdir -p "$LOG_DIR"
  "$PYTHON" -m dubsync >>"$LOG_DIR/launcher.log" 2>&1
  RC=$?
  if [[ "$RC" -ne 0 ]]; then
    MESSAGE="DubSync Studio konnte nicht gestartet werden. Protokoll: $LOG_DIR/launcher.log"
    if command -v notify-send >/dev/null 2>&1; then notify-send "DubSync Studio" "$MESSAGE"; fi
    echo "$MESSAGE" >&2
  fi
  exit "$RC"
fi
exec "$PYTHON" -m dubsync "$@"
'''
launcher.write_text(content, encoding="utf-8")
launcher.chmod(0o755)
PY

cp "$SOURCE_DIR/assets/dubsync-studio.svg" "$ICON_FILE"
chmod 644 "$ICON_FILE"
"$PYTHON" - "$LAUNCHER" "$ICON_FILE" "$DESKTOP_FILE" <<'PY'
from pathlib import Path
import sys
launcher, icon, destination = map(str, sys.argv[1:])
def quote(value: str) -> str:
    value = value.replace('%', '%%')  # preserve literal percent; avoid Exec field-code expansion
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$') + '"'
content = "\n".join([
    "[Desktop Entry]", "Version=1.0", "Type=Application", "Name=DubSync Studio",
    "GenericName=Mehrsprachige Audio-/Videosynchronisation",
    "Comment=Videofassungen analysieren und mehrsprachige MKV-Dateien erstellen",
    # Exec uses Desktop Entry quoting; TryExec is deliberately omitted because
    # it is a plain executable-path field, not an Exec command line. Quoting
    # TryExec paths caused KDE/SteamOS to treat the quotes as part of the path.
    "Exec=" + quote(launcher), "Icon=dubsync-studio",
    "Terminal=false", "StartupWMClass=dubsync-studio",
    "Categories=AudioVideo;Video;Audio;Utility;",
    "Keywords=Video;Audio;Synchronisation;MKV;Serie;Mehrsprachig;", "",
])
p = Path(destination)
p.write_text(content, encoding="utf-8")
p.chmod(0o644)
print("Desktop-Menüeintrag erstellt:", p)
PY

if [[ "$MAKE_DESKTOP_SHORTCUT" -eq 1 ]]; then
  DESKTOP_DIR=""
  if command -v xdg-user-dir >/dev/null 2>&1; then DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"; fi
  if [[ -z "$DESKTOP_DIR" || "$DESKTOP_DIR" == "$HOME" ]]; then DESKTOP_DIR="$HOME/Desktop"; fi
  mkdir -p "$DESKTOP_DIR"
  cp "$DESKTOP_FILE" "$DESKTOP_DIR/DubSync Studio.desktop"
  chmod +x "$DESKTOP_DIR/DubSync Studio.desktop"
  echo "Desktop-Verknüpfung erstellt: $DESKTOP_DIR/DubSync Studio.desktop"
  echo "Je nach Desktop ggf. Rechtsklick → Start erlauben/Vertrauen wählen."
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
fi
if command -v desktop-file-validate >/dev/null 2>&1; then
  if ! desktop-file-validate "$DESKTOP_FILE"; then
    echo "Fehler: Der erzeugte Desktop-Menüeintrag ist ungültig: $DESKTOP_FILE" >&2
    echo "Installation wurde nicht als erfolgreich bestätigt; bitte diesen Fehlerbericht sichern." >&2
    exit 1
  fi
  if [[ "$MAKE_DESKTOP_SHORTCUT" -eq 1 ]]; then
    desktop-file-validate "$DESKTOP_DIR/DubSync Studio.desktop"
  fi
else
  echo "Hinweis: desktop-file-validate nicht installiert; Desktop-Eintrag wurde mit Pflichtfeldern erstellt."
fi

printf '\nInstallation abgeschlossen.\n'
printf 'App: %s\n' "$APP_DIR"
printf 'Launcher: %s\n' "$LAUNCHER"
printf 'Anwendungsmenü: DubSync Studio\n'
printf 'Diagnose: %s --doctor\n' "$LAUNCHER"
printf 'Zum Deinstallieren: %s/uninstall.sh\n' "$APP_DIR"
printf 'Benutzerkonfiguration und Cache bleiben bei der Deinstallation erhalten.\n'
