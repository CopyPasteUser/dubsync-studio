#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"

os_id="unknown"
os_like=""
if [[ -r /etc/os-release ]]; then
  os_id=$(sed -n 's/^ID=//p' /etc/os-release | head -n1 | tr -d '"' | tr '[:upper:]' '[:lower:]')
  os_like=$(sed -n 's/^ID_LIKE=//p' /etc/os-release | head -n1 | tr -d '"' | tr '[:upper:]' '[:lower:]')
fi

package_hint() {
  local missing="$1"
  local family="$os_id $os_like"
  echo >&2
  echo "Benötigte Systemkomponente fehlt: $missing" >&2
  case " $family " in
    *" ubuntu "*|*" debian "*|*" linuxmint "*|*" pop "*)
      echo "Installationshinweis (Debian/Ubuntu/Mint): sudo apt install python3 python3-venv python3-pip ffmpeg" >&2 ;;
    *" fedora "*|*" rhel "*|*" rocky "*|*" almalinux "*)
      echo "Installationshinweis (Fedora/RHEL): sudo dnf install python3 python3-pip ffmpeg" >&2
      echo "Hinweis: FFmpeg kann auf manchen Fedora-Systemen ein zusätzliches Repository erfordern." >&2 ;;
    *" arch "*|*" manjaro "*|*" endeavouros "*)
      echo "Installationshinweis (Arch/Manjaro): sudo pacman -S python python-pip ffmpeg" >&2 ;;
    *" opensuse "*|*" opensuse-tumbleweed "*|*" suse "*)
      echo "Installationshinweis (openSUSE): sudo zypper install python3 python3-pip ffmpeg" >&2 ;;
    *" alpine "*)
      echo "Installationshinweis (Alpine): sudo apk add python3 py3-pip py3-virtualenv ffmpeg" >&2 ;;
    *)
      echo "Installiere Python 3, venv/pip und FFmpeg/FFprobe über den Paketmanager deiner Distribution." >&2 ;;
  esac
}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 wurde nicht gefunden (PYTHON=$PYTHON)." >&2
  package_hint "Python 3"
  exit 1
fi

if ! "$PYTHON" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
then
  echo "DubSync Studio benötigt Python 3.10 oder neuer. Gefunden: $($PYTHON --version 2>&1)" >&2
  exit 1
fi

for tool in ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    package_hint "$tool"
    exit 1
  fi
done

if [[ ! -x .venv/bin/python ]]; then
  if ! "$PYTHON" -m venv .venv; then
    echo "Die Python-venv-Komponente konnte nicht erstellt werden." >&2
    package_hint "Python venv / ensurepip"
    exit 1
  fi
fi

export PIP_DISABLE_PIP_VERSION_CHECK=1
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m compileall -q dubsync
printf '\nSetup abgeschlossen.\n'
printf 'Portable starten: ./run.sh\n'
printf 'Dauerhaft installieren (Anwendungsmenü): ./install.sh\n'
printf 'Systemdiagnose: ./.venv/bin/python -m dubsync --doctor\n'
