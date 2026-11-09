#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v cargo >/dev/null || { echo 'Rust/Cargo fehlen. Python/NumPy bleibt nutzbar.' >&2; exit 1; }
cargo build --release --locked --manifest-path native/Cargo.toml
mkdir -p dubsync/_native
case "$(uname -s)" in
  Linux) library=libdubsync_native.so ;;
  Darwin) library=libdubsync_native.dylib ;;
  *) echo 'Für diese Plattform bitte die Rust-Bibliothek manuell kopieren.' >&2; exit 1 ;;
esac
cp "native/target/release/$library" "dubsync/_native/$library"
echo "Rust-Engine bereit: dubsync/_native/$library"
