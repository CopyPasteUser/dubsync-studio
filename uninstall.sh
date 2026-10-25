#!/usr/bin/env bash
set -euo pipefail
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
DEFAULT_APP_HOME="$DATA_HOME/dubsync-studio"
DEFAULT_APP_DIR="$DEFAULT_APP_HOME/app"
LAUNCHER="$HOME/.local/bin/dubsync-studio"
DESKTOP_FILE="$DATA_HOME/applications/dubsync-studio.desktop"
ICON_FILE="$DATA_HOME/icons/hicolor/scalable/apps/dubsync-studio.svg"
INSTALL_META="$CONFIG_HOME/dubsync-studio/install.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PURGE=0

for arg in "$@"; do
  case "$arg" in
    --purge-data) PURGE=1 ;;
    --help|-h)
      echo "Verwendung: ./uninstall.sh [--purge-data]"
      echo "Beim Entfernen bleiben Einstellungen, Cache und Jobhistorie erhalten."
      echo "--purge-data löscht zusätzlich ~/.config/dubsync-studio, ~/.cache/dubsync-studio und ~/.local/state/dubsync-studio."
      exit 0 ;;
    *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
  esac
done

# Prefer the validated install directory containing this running uninstaller's marker.
APP_DIR=""
if [[ -f "$SCRIPT_DIR/.dubsync-install.json" && -f "$SCRIPT_DIR/dubsync/__init__.py" && -f "$SCRIPT_DIR/pyproject.toml" ]]; then
  APP_DIR="$SCRIPT_DIR"
elif [[ -r "$INSTALL_META" ]]; then
  APP_DIR="$(python3 - "$INSTALL_META" 2>/dev/null <<'PY' || true
import json, sys
from pathlib import Path
try:
    meta = Path(sys.argv[1])
    data = json.loads(meta.read_text(encoding="utf-8"))
    target = Path(str(data.get("install_dir", ""))).expanduser().resolve(strict=False)
    marker = target / ".dubsync-install.json"
    marker_data = json.loads(marker.read_text(encoding="utf-8"))
    if (target.is_absolute() and target != Path("/") and target != Path.home().resolve()
        and marker_data.get("managed_by") == "dubsync-studio-installer"
        and Path(str(marker_data.get("install_dir", ""))).resolve(strict=False) == target
        and (target / "dubsync" / "__init__.py").is_file()
        and (target / "pyproject.toml").is_file()):
        print(target)
except Exception:
    pass
PY
)"
fi
if [[ -z "$APP_DIR" ]]; then APP_DIR="$DEFAULT_APP_DIR"; fi

# Defense in depth: never remove broad directories or app data roots.
SAFE_APP_DIR="$(python3 - "$APP_DIR" "$HOME" "$CONFIG_HOME" "$CACHE_HOME" "$STATE_HOME" "$DATA_HOME" <<'PY'
from pathlib import Path
import sys
candidate = Path(sys.argv[1]).expanduser().resolve(strict=False)
home = Path(sys.argv[2]).expanduser().resolve(strict=False)
config = Path(sys.argv[3]).expanduser().resolve(strict=False)
cache = Path(sys.argv[4]).expanduser().resolve(strict=False)
state = Path(sys.argv[5]).expanduser().resolve(strict=False)
data = Path(sys.argv[6]).expanduser().resolve(strict=False)
exact_protected = {Path("/"), home, config, cache, state, data,
                   home / ".local", home / ".local" / "bin",
                   data / "applications", data / "icons"}
if candidate in exact_protected:
    raise SystemExit("Unsicheres Deinstallationsziel; nichts wurde entfernt.")
if any(base == candidate or base in candidate.parents for base in (config, cache, state, data / "applications", data / "icons")):
    raise SystemExit("Deinstallationsziel liegt in einem geschützten Benutzerordner; nichts wurde entfernt.")
print(candidate)
PY
)"
APP_DIR="$SAFE_APP_DIR"
IS_DEFAULT=0
if [[ "$APP_DIR" == "$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve(strict=False))' "$DEFAULT_APP_DIR")" ]]; then
  IS_DEFAULT=1
fi

rm -f "$LAUNCHER" "$DESKTOP_FILE" "$ICON_FILE"
if [[ -f "$HOME/Desktop/DubSync Studio.desktop" ]]; then rm -f "$HOME/Desktop/DubSync Studio.desktop"; fi
if command -v xdg-user-dir >/dev/null 2>&1; then
  DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
  if [[ -n "$DESKTOP_DIR" && -f "$DESKTOP_DIR/DubSync Studio.desktop" ]]; then rm -f "$DESKTOP_DIR/DubSync Studio.desktop"; fi
fi
if [[ -d "$APP_DIR" ]]; then rm -rf -- "$APP_DIR"; fi
if [[ -f "$INSTALL_META" ]]; then rm -f "$INSTALL_META"; fi
if [[ "$IS_DEFAULT" -eq 1 ]]; then rmdir "$DEFAULT_APP_HOME" 2>/dev/null || true; fi
if [[ "$PURGE" -eq 1 ]]; then
  rm -rf -- "$CONFIG_HOME/dubsync-studio" "$CACHE_HOME/dubsync-studio" "$STATE_HOME/dubsync-studio"
  echo "Anwendung und Benutzerdaten entfernt."
else
  echo "Anwendung entfernt. Einstellungen, Cache und Jobhistorie wurden beibehalten."
  echo "Für vollständige Datenlöschung ./uninstall.sh --purge-data verwenden."
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DATA_HOME/applications" >/dev/null 2>&1 || true
fi
