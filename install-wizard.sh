#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 wurde nicht gefunden. Bitte Python 3.10+ installieren." >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
  echo "DubSync Studio benötigt Python 3.10 oder neuer." >&2
  exit 1
fi
if ! "$PYTHON" -c 'import tkinter, tkinter.ttk' >/dev/null 2>&1; then
  echo "Tkinter fehlt. Installiere das Tk-Paket deiner Distribution (z. B. python3-tk) oder nutze ./install.sh für die Terminalinstallation." >&2
  exit 2
fi
exec "$PYTHON" "$SOURCE_DIR/installer_wizard.py" "$@"
