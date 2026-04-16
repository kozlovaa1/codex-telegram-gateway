from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from codex_telegram_gateway.app import GatewayApp, display_workspace_name, make_session_workspace_name, supports_topic_creation
from codex_telegram_gateway.config import AppConfig, TelegramSettings
from codex_telegram_gateway.models import ChatScope
from codex_telegram_gateway.workspace_store import WorkspaceStore


class DummySessions:
    def runtime_snapshot(self):
        return []


class DummyTelegram:
    pass


class AppDefaultWorkspaceTests(unittest.TestCase):
    def test_unbound_scope_uses_default_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(
                Path(tmp) / "db.sqlite3",
                {"server-ops": "/srv/projects"},
                None,
                "workspace-write",
                "never",
            )
            store.initialize()
            config = AppConfig(
                bot_name="test",
                telegram_api_base="https://api.telegram.org",
                telegram_token="token",
                telegram_admin_ids=set(),
                poll_timeout_seconds=10,
                poll_retry_delay_seconds=1,
                telegram_message_chunk=3900,
                stream_edit_interval_seconds=2.0,
                status_port=8085,
                sqlite_path=Path(tmp) / "db.sqlite3",
                runtime_dir=Path(tmp) / "runtime",
                log_dir=Path(tmp) / "log",
                codex_bin="/bin/true",
                codex_auth_source_home=None,
                default_workspace_name="server-ops",
                default_model=None,
                default_sandbox_mode="workspace-write",
                default_approval_policy="never",
                session_idle_ttl_seconds=60,
                command_timeout_seconds=60,
                process_kill_grace_seconds=1,
                max_parallel_processes=1,
                max_queue_per_workspace=1,
                max_active_workspaces=4,
                per_user_rate_limit_window_seconds=10,
                per_user_rate_limit_max_messages=5,
                trusted_admin_only_bind=True,
                allowed_roots=[Path("/srv/projects")],
                project_alias_roots=[Path("/srv/projects")],
                workspace_defaults={"server-ops": "/srv/projects"},
                telegram=TelegramSettings(True, True, True),
            )
            app = GatewayApp(config, store, DummySessions(), DummyTelegram(), logging.getLogger("test"))
            resolved = app._workspace_from_scope(ChatScope(chat_id=1, thread_id=None))
            self.assertEqual(resolved, ("server-ops", "/srv/projects"))

    def test_display_workspace_name_hides_internal_topic_session_name(self) -> None:
        internal = make_session_workspace_name("infra", -100123, 55)
        self.assertEqual(display_workspace_name(internal), "infra")

    def test_supports_topic_creation_for_private_threaded_chats(self) -> None:
        self.assertTrue(supports_topic_creation("private", False))
        self.assertTrue(supports_topic_creation("supergroup", True))
        self.assertFalse(supports_topic_creation("group", False))


if __name__ == "__main__":
    unittest.main()
