"""The startup timing helper used for SUPRMAN_TIMING=1."""

from __future__ import annotations

import io
import time
import unittest
from contextlib import redirect_stderr
from unittest import mock

from suprman import timing


class TimingTest(unittest.TestCase):
    def test_process_age_is_small(self) -> None:
        age = timing._process_age()
        # A test process is seconds old; uptime (the usual mis-measurement) is
        # hours or days, so this catches the clock mixup that broke v0.1 timing.
        self.assertGreaterEqual(age, 0.0)
        self.assertLess(age, 120.0)

    def test_start_is_on_the_monotonic_clock(self) -> None:
        self.assertLessEqual(timing._START, time.monotonic())
        self.assertGreater(timing._START, time.monotonic() - 120.0)

    def test_disabled_by_default_writes_nothing(self) -> None:
        buffer = io.StringIO()
        with mock.patch.object(timing, "ENABLED", False), redirect_stderr(buffer):
            timing.mark("hello")
        self.assertEqual(buffer.getvalue(), "")

    def test_enabled_writes_label_and_total(self) -> None:
        buffer = io.StringIO()
        with mock.patch.object(timing, "ENABLED", True), redirect_stderr(buffer):
            timing.mark("startup")
        output = buffer.getvalue()
        self.assertIn("startup", output)
        self.assertIn("ms", output)


if __name__ == "__main__":
    unittest.main()
