#!/usr/bin/env bash
# Backwards-compatible entry point; use the portable user installer.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
exec "$SCRIPT_DIR/install.sh" "$@"
