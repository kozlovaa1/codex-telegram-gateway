from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_telegram_gateway.workspace_preflight import WorkspacePreflightChecker


class WorkspacePreflightTests(unittest.TestCase):
    def test_preflight_accepts_workspace_and_prepares_codex_dir(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            workspace.mkdir()
            checker = WorkspacePreflightChecker([Path(root)])

            result = checker.run("demo", str(workspace))

            self.assertTrue(result.ok)
            self.assertTrue((workspace / ".codex").is_dir())
            self.assertEqual(result.canonical_path, str(workspace.resolve()))

    def test_preflight_rejects_symlink_escape_outside_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            escaped = Path(outside) / "escaped"
            escaped.mkdir()
            link = Path(root) / "link"
            link.symlink_to(escaped, target_is_directory=True)
            checker = WorkspacePreflightChecker([Path(root)])

            result = checker.run("demo", str(link))

            self.assertFalse(result.ok)
            self.assertEqual(result.diagnostics[0].check_name, "path_security")

    def test_preflight_reports_write_probe_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            workspace.mkdir()
            checker = WorkspacePreflightChecker([Path(root)])

            with patch("pathlib.Path.write_text", side_effect=OSError("denied")):
                result = checker.run("demo", str(workspace))

            self.assertFalse(result.ok)
            self.assertEqual(result.diagnostics[-1].check_name, "write_probe")


if __name__ == "__main__":
    unittest.main()
