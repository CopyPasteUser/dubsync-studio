"""Reject universal wheels contaminated by local host-native build artifacts."""
from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile


_LIBRARIES = (".so", ".dylib", ".dll", ".pyd")


def verify_wheel(path: Path) -> None:
    if not path.is_file() or path.suffix != ".whl":
        raise ValueError(f"Wheel nicht gefunden: {path}")
    with ZipFile(path) as archive:
        wheel_names = [name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")]
        if len(wheel_names) != 1:
            raise ValueError("Ungültiges Wheel-Metadatenverzeichnis")
        metadata = archive.read(wheel_names[0]).decode("utf-8")
        portable = any(line.startswith("Tag: ") and line.strip().endswith("-none-any")
                       for line in metadata.splitlines())
        embedded = [name for name in archive.namelist()
                    if name.casefold().endswith(_LIBRARIES) or ".so." in name.casefold()]
        if portable and embedded:
            raise ValueError("Plattformgebundene Bibliothek in universalem Wheel: " +
                             ", ".join(embedded[:6]))
        if "dubsync/assets/dubsync-studio.svg" not in archive.namelist():
            raise ValueError("Markenlogo fehlt im installierbaren Wheel")


def main(argv: list[str]) -> int:
    if not argv:
        print("Aufruf: python scripts/verify_wheel.py WHEEL.whl ...", file=sys.stderr)
        return 2
    for value in argv:
        path = Path(value)
        try:
            verify_wheel(path)
        except (OSError, ValueError) as exc:
            print(f"Wheel-Prüfung fehlgeschlagen: {exc}", file=sys.stderr)
            return 1
        print(f"Portabilität geprüft: {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
