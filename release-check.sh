#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"
REQUIRE_GUI=0
if [[ "${1:-}" == "--require-gui" ]]; then REQUIRE_GUI=1; fi
PYTHON="${PYTHON:-python3}"
"$PYTHON" -m compileall -q dubsync || { echo "COMPILE FAILED"; exit 1; }
"$PYTHON" -m unittest discover -s tests -v || { echo "TESTS FAILED"; exit 1; }
"$PYTHON" scripts/release_check.py --require-gui "$REQUIRE_GUI"
