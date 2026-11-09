"""Guard against silently shipping a Linux Rust binary in a universal wheel."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.verify_wheel import verify_wheel


class WheelPortabilityTests(unittest.TestCase):
    def _make_wheel(self, root: Path, *, platform_tag="py3-none-any",
                    native=False, logo=True):
        wheel = root / f"dubsync_studio-3.0.0b1-{platform_tag}.whl"
        with ZipFile(wheel, "w") as archive:
            archive.writestr("dubsync_studio-3.0.0b1.dist-info/WHEEL",
                             f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {platform_tag}\n")
            if logo:
                archive.writestr("dubsync/assets/dubsync-studio.svg", "<svg/>")
            if native:
                archive.writestr("dubsync/_native/libdubsync_native.so", b"ELF test")
        return wheel

    def test_pure_wheel_keeps_logo_and_no_native_binary(self):
        with tempfile.TemporaryDirectory() as temp:
            verify_wheel(self._make_wheel(Path(temp)))

    def test_universal_binary_wheel_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "Plattformgebundene"):
                verify_wheel(self._make_wheel(Path(temp), native=True))

    def test_platform_specific_binary_wheel_can_be_explicitly_tagged(self):
        with tempfile.TemporaryDirectory() as temp:
            verify_wheel(self._make_wheel(Path(temp), platform_tag="cp312-cp312-manylinux_x86_64",
                                          native=True))

    def test_missing_logo_fails_packaging_check(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "Markenlogo"):
                verify_wheel(self._make_wheel(Path(temp), logo=False))


if __name__ == "__main__":
    unittest.main()
