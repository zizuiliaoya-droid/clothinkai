"""Synthetic failure diagnostics only; never connects to Docker or PostgreSQL."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import run_acceptance_pg as entry


class PgEntryDiagnostics(unittest.TestCase):
    def test_sql_failure_keeps_bounded_stderr_and_does_not_retry(self):
        error = subprocess.CalledProcessError(
            3, ("synthetic",), stderr=b"x" * 9000 + b"ERROR cannot reassign"
        )
        output = io.StringIO()
        with (
            patch.object(
                entry.subprocess, "check_output", side_effect=error
            ) as execute,
            contextlib.redirect_stderr(output),
            self.assertRaises(subprocess.CalledProcessError) as raised,
        ):
            entry.call("exec", "a" * 64, "psql", stdin=b"synthetic SQL only")
        self.assertIs(raised.exception, error)
        execute.assert_called_once()
        row = json.loads(output.getvalue())
        self.assertEqual(row["exit_code"], 3)
        self.assertTrue(row["stderr_truncated"])
        self.assertEqual(len(row["stderr_tail"].encode()), 8192)
        self.assertTrue(row["stderr_tail"].endswith("ERROR cannot reassign"))
        self.assertNotIn("synthetic SQL only", output.getvalue())

    def test_timeout_keeps_diagnostic_and_remains_failure(self):
        error = subprocess.TimeoutExpired(
            ("synthetic",), 1, stderr=b"bounded timeout detail"
        )
        output = io.StringIO()
        with (
            patch.object(entry.subprocess, "check_output", side_effect=error),
            contextlib.redirect_stderr(output),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            entry.call("inspect", "a" * 64, timeout=1)
        row = json.loads(output.getvalue())
        self.assertEqual(row["error_type"], "TimeoutExpired")
        self.assertFalse(row["stderr_truncated"])
        self.assertEqual(row["stderr_tail"], "bounded timeout detail")


class RestoreFailureDiagnostics(unittest.TestCase):
    def complete(self, operation):
        try:
            operation.send(None)
        except StopIteration as completed:
            return completed.value
        finally:
            operation.close()
        self.fail("unexpected I/O outside immediate mocked captures")

    def test_exact_owned_id_only_and_bounded_combined_logs(self):
        identity, image = "a" * 64, "sha256:" + "b" * 64
        state = {"id": identity, "image": image, "status": "exited", "exit_code": 1}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.object(
                entry.backup,
                "_capture",
                AsyncMock(
                    side_effect=[json.dumps(state).encode(), b"permission denied\n"]
                ),
            ) as capture:
                self.complete(
                    entry.restore_failure_evidence(identity, image, output, output)
                )
            self.assertEqual(capture.await_count, 2)
            inspect = capture.await_args_list[0].args[0]
            self.assertEqual(inspect[-1], identity)
            self.assertNotIn("Env", " ".join(inspect))
            logs = capture.await_args_list[1].args[0]
            self.assertEqual(logs[-1], identity)
            self.assertIn('logs --tail 80 "$1" 2>&1 | /usr/bin/head -c 65536', logs[-3])
            self.assertEqual(
                (output / "restore-failure.log").read_bytes(), b"permission denied\n"
            )
            self.assertEqual(
                json.loads((output / "restore-failure-state.json").read_bytes())[
                    "container"
                ],
                state,
            )

    def test_foreign_identity_blocks_log_read_and_oversize_is_rejected(self):
        identity, image = "a" * 64, "sha256:" + "b" * 64
        for foreign in (True, False):
            with (
                self.subTest(foreign=foreign),
                tempfile.TemporaryDirectory() as temporary,
            ):
                output = Path(temporary)
                state = {"id": "c" * 64 if foreign else identity, "image": image}
                with (
                    patch.object(
                        entry.backup,
                        "_capture",
                        AsyncMock(
                            side_effect=[json.dumps(state).encode(), b"x" * 65537]
                        ),
                    ) as capture,
                    self.assertRaises(entry.Rejected),
                ):
                    self.complete(
                        entry.restore_failure_evidence(identity, image, output, output)
                    )
                self.assertEqual(capture.await_count, 1 if foreign else 2)
                self.assertFalse((output / "restore-failure.log").exists())


if __name__ == "__main__":
    unittest.main()
