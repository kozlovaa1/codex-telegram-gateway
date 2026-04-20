from __future__ import annotations

import sqlite3
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
            store = WorkspaceStore(
                Path(tmp) / "db.sqlite3",
                {"infra": "/srv/infra"},
                "gpt-5",
                "workspace-write",
                "never",
                "restricted",
            )
            store.initialize()
            session = store.get_session("infra")
            self.assertEqual(session.model, "gpt-5")
            self.assertEqual(session.sandbox_mode, "workspace-write")
            self.assertEqual(session.profile_name, "default")
            self.assertEqual(session.network_mode, "restricted")
            self.assertEqual(session.command_rule_set_version, 1)
            self.assertEqual(session.busy_state, "idle")

    def test_binding_without_thread_uses_zero_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"infra": "/srv/infra"}, None, "workspace-write", "never")
            store.initialize()
            scope = ChatScope(chat_id=7, thread_id=None)
            store.bind_scope(scope, "infra")
            binding = store.get_binding(scope)
            self.assertIsNotNone(binding)
            self.assertEqual(binding.thread_id, None)

    def test_initialize_migrates_legacy_sessions_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "db.sqlite3"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE workspaces (
                        name TEXT PRIMARY KEY,
                        path TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE sessions (
                        workspace_name TEXT PRIMARY KEY,
                        session_id TEXT,
                        model TEXT,
                        sandbox_mode TEXT NOT NULL,
                        approval_policy TEXT NOT NULL,
                        last_used_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO workspaces(name, path, created_at, updated_at)
                    VALUES('infra', '/srv/infra', '2026-04-20T10:00:00Z', '2026-04-20T10:00:00Z');
                    INSERT INTO sessions(workspace_name, session_id, model, sandbox_mode, approval_policy, last_used_at, created_at, updated_at)
                    VALUES('infra', 'thread-1', 'gpt-5', 'workspace-write', 'never', '2026-04-20T10:05:00Z', '2026-04-20T10:00:00Z', '2026-04-20T10:06:00Z');
                    """
                )
            store = WorkspaceStore(db_path, {"infra": "/srv/infra"}, "gpt-5", "workspace-write", "never", "restricted")
            store.initialize()
            session = store.get_session("infra")
            self.assertEqual(session.session_id, "thread-1")
            self.assertEqual(session.model, "gpt-5")
            self.assertEqual(session.sandbox_mode, "workspace-write")
            self.assertEqual(session.approval_policy, "never")
            self.assertEqual(session.network_mode, "restricted")
            self.assertEqual(session.last_used_at, "2026-04-20T10:05:00Z")

    def test_reset_session_normalizes_empty_session_id_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"infra": "/srv/infra"}, None, "workspace-write", "never")
            store.initialize()
            store.update_session("infra", session_id="session-1")

            session = store.reset_session("infra")

            self.assertIsNone(session.session_id)

    def test_policy_changes_mark_durable_override_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"infra": "/srv/infra"}, None, "workspace-write", "never")
            store.initialize()

            session = store.update_session("infra", sandbox_mode="read-only")

            self.assertEqual(session.execution_policy.override_scope, "durable-override")
            self.assertEqual(session.sandbox_mode, "read-only")


if __name__ == "__main__":
    unittest.main()
