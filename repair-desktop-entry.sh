#!/usr/bin/env bash
# Regenerate the DubSync Studio launcher in a freedesktop/KDE-compatible form.
set -euo pipefail

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
APP_LAUNCHER="$HOME/.local/bin/dubsync-studio"
APP_ENTRY="$DATA_HOME/applications/dubsync-studio.desktop"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
ICON_FILE="$ICON_DIR/dubsync-studio.svg"

if [[ ! -x "$APP_LAUNCHER" ]]; then
  echo "DubSync Studio launcher not found or not executable: $APP_LAUNCHER" >&2
  echo "Install or update the app with ./install-wizard.sh first." >&2
  exit 1
fi

mkdir -p "$(dirname "$APP_ENTRY")" "$ICON_DIR"

# Recover the app asset into the standard user icon theme if the prior installer
# created an entry but missed the icon installation.
if [[ ! -s "$ICON_FILE" ]]; then
  APP_DIR="$DATA_HOME/dubsync-studio/app"
  if [[ -r "$CONFIG_HOME/dubsync-studio/install.json" ]]; then
    META_APP_DIR=$(python3 - "$CONFIG_HOME/dubsync-studio/install.json" <<'PY'
import json, sys
from pathlib import Path
try:
    data=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    path=Path(str(data.get("install_dir", ""))).expanduser()
    if path.is_absolute(): print(path)
except Exception: pass
PY
)
    if [[ -n "$META_APP_DIR" && -f "$META_APP_DIR/assets/dubsync-studio.svg" ]]; then
      APP_DIR="$META_APP_DIR"
    fi
  fi
  if [[ -s "$APP_DIR/assets/dubsync-studio.svg" ]]; then
    install -m 0644 "$APP_DIR/assets/dubsync-studio.svg" "$ICON_FILE"
  else
    echo "Icon file not found: $ICON_FILE (and no installed app icon asset found)." >&2
    exit 1
  fi
fi

TMP_ENTRY=$(mktemp "$DATA_HOME/applications/.dubsync-studio.XXXXXX.desktop")
TMP_SHORTCUT=""
cleanup() {
  [[ -z "$TMP_ENTRY" || ! -e "$TMP_ENTRY" ]] || rm -f "$TMP_ENTRY"
  [[ -z "$TMP_SHORTCUT" || ! -e "$TMP_SHORTCUT" ]] || rm -f "$TMP_SHORTCUT"
}
trap cleanup EXIT

python3 - "$APP_LAUNCHER" "$TMP_ENTRY" <<'PY'
from pathlib import Path
import configparser, sys
launcher, output = sys.argv[1:]
def desktop_exec_arg(value: str) -> str:
    # Percent signs are field-code escapes in Exec, so %% represents a literal %.
    value = value.replace("%", "%%")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'
content = "\n".join([
    "[Desktop Entry]",
    "Version=1.0",
    "Type=Application",
    "Name=DubSync Studio",
    "GenericName=Mehrsprachige Audio-/Videosynchronisation",
    "Comment=Videofassungen analysieren und mehrsprachige MKV-Dateien erstellen",
    "Exec=" + desktop_exec_arg(launcher),
    "Icon=dubsync-studio",
    "Terminal=false",
    "StartupWMClass=dubsync-studio",
    "Categories=AudioVideo;Video;Audio;Utility;",
    "Keywords=Video;Audio;Synchronisation;MKV;Serie;Mehrsprachig;",
    "",
])
# Basic structural validation before any existing entry gets replaced.
p = configparser.ConfigParser(interpolation=None, strict=True)
p.optionxform = str
p.read_string(content)
entry = p["Desktop Entry"]
for key in ("Type", "Name", "Exec", "Icon", "Terminal", "Categories"):
    if not entry.get(key):
        raise SystemExit("Required desktop key missing: " + key)
if entry["Type"] != "Application" or entry["Terminal"] != "false":
    raise SystemExit("Desktop entry has invalid Type/Terminal values")
Path(output).write_text(content, encoding="utf-8")
PY
chmod 0644 "$TMP_ENTRY"

if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$TMP_ENTRY"
else
  echo "Note: desktop-file-validate is not installed; basic entry validation passed."
fi

stamp=$(date +%Y%m%d-%H%M%S)
if [[ -e "$APP_ENTRY" ]]; then
  cp -p "$APP_ENTRY" "$APP_ENTRY.bak.$stamp"
fi
mv -f "$TMP_ENTRY" "$APP_ENTRY"
TMP_ENTRY=""
echo "Fixed application menu entry: $APP_ENTRY"

# Repair a previously created desktop shortcut too, but do not create one
# unexpectedly if the user never asked for a desktop icon.
DESKTOP_DIR="${XDG_DESKTOP_DIR:-}"
if [[ -z "$DESKTOP_DIR" ]] && command -v xdg-user-dir >/dev/null 2>&1; then
  DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || true)
fi
if [[ -z "$DESKTOP_DIR" || "$DESKTOP_DIR" == "$HOME" ]]; then
  DESKTOP_DIR="$HOME/Desktop"
fi
SHORTCUT="$DESKTOP_DIR/DubSync Studio.desktop"
if [[ -f "$SHORTCUT" ]]; then
  TMP_SHORTCUT=$(mktemp "$DESKTOP_DIR/.dubsync-studio.XXXXXX.desktop")
  cp "$APP_ENTRY" "$TMP_SHORTCUT"
  chmod 0755 "$TMP_SHORTCUT"
  if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$TMP_SHORTCUT"
  fi
  cp -p "$SHORTCUT" "$SHORTCUT.bak.$stamp"
  mv -f "$TMP_SHORTCUT" "$SHORTCUT"
  TMP_SHORTCUT=""
  echo "Fixed desktop shortcut: $SHORTCUT"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$(dirname "$APP_ENTRY")" >/dev/null 2>&1 || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
  kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
  kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
fi

echo "DubSync Studio desktop integration repaired. Relaunch it from the application menu."
