from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_telegram_gateway.models import ChatScope
from codex_telegram_gateway.workspace_store import WorkspaceStore


class WorkspaceStoreTests(unittest.TestCase):
    def test_bind_and_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"infra": "/srv/infra"}, None, "workspace-write", "never")
            store.initialize()
            store.upsert_workspace("demo", "/srv/projects/demo")
            scope = ChatScope(chat_id=1, thread_id=2)
            store.bind_scope(scope, "demo")
            binding = store.get_binding(scope)
            self.assertIsNotNone(binding)
            self.assertEqual(binding.workspace_name, "demo")

    def test_session_defaults_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"infra": "/srv/infra"}, "gpt-5", "workspace-write", "never")
            store.initialize()
            session = store.get_session("infra")
            self.assertEqual(session.model, "gpt-5")
            self.assertEqual(session.sandbox_mode, "workspace-write")

    def test_binding_without_thread_uses_zero_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"infra": "/srv/infra"}, None, "workspace-write", "never")
            store.initialize()
            scope = ChatScope(chat_id=7, thread_id=None)
            store.bind_scope(scope, "infra")
            binding = store.get_binding(scope)
            self.assertIsNotNone(binding)
            self.assertEqual(binding.thread_id, None)


if __name__ == "__main__":
    unittest.main()
