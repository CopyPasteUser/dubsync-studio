from __future__ import annotations

import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from dubsync.core.logging_setup import configure_logging


class LoggingSetupTests(unittest.TestCase):
    def test_rotating_log_handler_writes_user_diagnostic_file(self):
        old_hook = sys.excepthook
        old_thread_hook = getattr(threading, "excepthook", None)
        logger = logging.getLogger("dubsync")
        old_handlers = list(logger.handlers)
        try:
            for handler in old_handlers:
                logger.removeHandler(handler)
                handler.close()
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "logs" / "dubsync.log"
                configured = configure_logging(path)
                configured.info("release-smoke-test-message")
                for handler in configured.handlers:
                    handler.flush()
                self.assertTrue(path.is_file())
                self.assertIn("release-smoke-test-message", path.read_text(encoding="utf-8"))
                if path.stat().st_mode & 0o777:
                    # Windows-like permission semantics may vary; on POSIX this is strict.
                    import os
                    if os.name == "posix":
                        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in old_handlers:
                logger.addHandler(handler)
            sys.excepthook = old_hook
            if old_thread_hook is not None:
                threading.excepthook = old_thread_hook


if __name__ == "__main__":
    unittest.main(verbosity=2)
