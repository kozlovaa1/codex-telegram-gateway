from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_telegram_gateway.path_security import PathSecurityError, alias_for_project, resolve_workspace_path


class PathSecurityTests(unittest.TestCase):
    def test_accepts_path_within_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            workspace.mkdir()
            resolved = resolve_workspace_path(str(workspace), [Path(root)])
            self.assertEqual(resolved, workspace.resolve())

    def test_rejects_path_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            with self.assertRaises(PathSecurityError):
                resolve_workspace_path(outside, [Path(root)])

    def test_alias_for_project(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "demo"
            project.mkdir()
            self.assertEqual(alias_for_project(project, [Path(root)]), "project:demo")


if __name__ == "__main__":
    unittest.main()
