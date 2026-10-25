"""Synthetic failure diagnostics only; never connects to Docker or PostgreSQL."""

import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
