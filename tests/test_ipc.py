"""The resident-instance socket, tested with real sockets (no GTK)."""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from suprman import ipc


class OneShotServer:
    """Accepts a single connection, records what it said, then closes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.received: list[str] = []
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(path))
        self._sock.listen(1)
        self._sock.settimeout(5.0)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            connection, _ = self._sock.accept()
        except (TimeoutError, OSError):
            return
        with connection:
            self.received.append(connection.recv(64).decode("utf-8").strip())
        self._sock.close()

    def join(self, timeout: float = 5.0) -> None:
        self._thread.join(timeout)

    def cleanup(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.close()
        with contextlib.suppress(OSError):
            self.path.unlink()


class IpcTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_socket_path_lives_in_the_runtime_dir(self) -> None:
        self.assertEqual(ipc.socket_path(), self.runtime / ipc.SOCKET_FILENAME)

    def test_send_returns_false_without_a_server(self) -> None:
        self.assertFalse(ipc.send("toggle"))

    def test_send_delivers_the_command(self) -> None:
        server = OneShotServer(ipc.socket_path())
        self.addCleanup(server.cleanup)
        self.assertTrue(ipc.send("toggle"))
        server.join()
        self.assertEqual(server.received, ["toggle"])

    def test_stale_socket_file_is_not_fatal(self) -> None:
        # A crash leaves the file behind; connect() must fail cleanly.
        path = ipc.socket_path()
        path.write_text("not a socket", encoding="utf-8")
        self.assertFalse(ipc.send("toggle"))

    def test_try_handoff_sends_toggle_for_a_plain_launch(self) -> None:
        server = OneShotServer(ipc.socket_path())
        self.addCleanup(server.cleanup)
        self.assertTrue(ipc.try_handoff(["suprman"]))
        server.join()
        self.assertEqual(server.received, ["toggle"])

    def test_try_handoff_steps_aside_for_flags(self) -> None:
        server = OneShotServer(ipc.socket_path())
        self.addCleanup(server.cleanup)
        for argv in (
            ["suprman", "--serve"],
            ["suprman", "--quit"],
            ["suprman", "-c", "/tmp/x.conf"],
            ["suprman", "--example-config"],
        ):
            self.assertFalse(ipc.try_handoff(argv), argv)
        self.assertEqual(server.received, [])

    def test_parse_command(self) -> None:
        self.assertEqual(ipc.parse_command("toggle\n"), "toggle")
        self.assertEqual(ipc.parse_command("  SHOW "), "show")
        self.assertIsNone(ipc.parse_command("rm -rf /"))
        self.assertIsNone(ipc.parse_command(""))

    def test_missing_runtime_dir_falls_back_to_cache(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            path = ipc.socket_path()
        self.assertEqual(path.name, ipc.SOCKET_FILENAME)
        self.assertTrue(path.parent.name == "suprman")


class SpawnTest(unittest.TestCase):
    """Warming: leave a resident instance behind after a cold start."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)})
        patcher.start()
        self.addCleanup(patcher.stop)
        # spawn_server() inspects sys.argv[0]; point it at a stand-in launcher.
        self.marker = self.runtime / "spawned.txt"
        self.launcher = self.runtime / "fake-launcher"
        self.launcher.write_text(
            "import pathlib, sys\n"
            f"pathlib.Path({str(self.marker)!r}).write_text(' '.join(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        argv = mock.patch.object(sys, "argv", [str(self.launcher)])
        argv.start()
        self.addCleanup(argv.stop)

    def test_spawns_a_detached_server_with_an_idle_timeout(self) -> None:
        self.assertTrue(ipc.spawn_server(idle_minutes=15))
        deadline = time.monotonic() + 10
        while not self.marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.marker.exists(), "child never ran")
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "--serve --idle-exit 15")

    def test_does_not_spawn_when_one_is_already_listening(self) -> None:
        server = OneShotServer(ipc.socket_path())
        self.addCleanup(server.cleanup)
        self.assertFalse(ipc.spawn_server())
        server.join()
        self.assertEqual(server.received, ["ping"])
        self.assertFalse(self.marker.exists())

    def test_second_spawn_is_skipped_while_one_is_starting(self) -> None:
        self.assertTrue(ipc.spawn_server())
        self.assertFalse(ipc.spawn_server(), "spawned twice in a row")
        ipc.clear_spawn_marker()

    def test_marker_expires(self) -> None:
        marker = self.runtime / ipc.SPAWN_MARKER
        marker.write_text("", encoding="utf-8")
        old = time.time() - ipc.SPAWN_GUARD_SECONDS - 5
        os.utime(marker, (old, old))
        self.assertTrue(ipc.spawn_server(), "guard ignored an old marker")

    def test_refuses_to_spawn_from_a_module_run(self) -> None:
        with mock.patch.object(sys, "argv", ["/somewhere/suprman/__main__.py"]):
            self.assertFalse(ipc.spawn_server())
        self.assertFalse(self.marker.exists())


class HandoffCostTest(unittest.TestCase):
    """The hotkey path must not import GTK."""

    def test_ipc_imports_without_gi(self) -> None:
        import subprocess
        import sys

        source = Path(__file__).resolve().parent.parent / "src"
        code = (
            f"import sys; sys.path.insert(0, {str(source)!r});"
            "import suprman.ipc;"
            "assert 'gi' not in sys.modules, sorted(m for m in sys.modules if m.startswith('gi'));"
            "print('ok')"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_launcher_exits_fast_when_a_server_answers(self) -> None:
        import subprocess
        import sys

        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            server = OneShotServer(runtime / ipc.SOCKET_FILENAME)
            self.addCleanup(server.cleanup)
            environment = dict(os.environ, XDG_RUNTIME_DIR=str(runtime))
            launcher = Path(__file__).resolve().parent.parent / "bin" / "suprman"
            started = time.perf_counter()
            result = subprocess.run(
                [sys.executable, str(launcher)],
                capture_output=True,
                text=True,
                env=environment,
                timeout=30,
            )
            elapsed = time.perf_counter() - started
            server.join()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(server.received, ["toggle"])
        self.assertLess(elapsed, 1.0, f"handoff took {elapsed * 1000:.0f} ms")


if __name__ == "__main__":
    unittest.main()
