from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from codex_telegram_gateway.app import GatewayApp
from codex_telegram_gateway.config import AdminOnlySettings, AppConfig, ExecutionProfile, TelegramSettings, default_response_ux_settings
from codex_telegram_gateway.execution_policy import ExecutionPolicyResolver
from codex_telegram_gateway.models import ChatScope
from codex_telegram_gateway.workspace_preflight import PreflightDiagnostic, WorkspacePreflightError, WorkspacePreflightResult
from codex_telegram_gateway.workspace_store import WorkspaceStore


class FakeSessions:
    def __init__(self, restart_error: Exception | None = None) -> None:
        self.policy_changes: list[dict[str, object]] = []
        self.restarts: list[tuple[str, str, str]] = []
        self.restart_error = restart_error
        self.stops: list[str] = []

    def runtime_snapshot(self):
        return []

    async def apply_policy_change(self, workspace_name: str, workspace_path: str, **kwargs):
        self.policy_changes.append({"workspace_name": workspace_name, "workspace_path": workspace_path, **kwargs})
        class Session:
            profile_name = kwargs.get("profile_name", "default")
            sandbox_mode = kwargs.get("sandbox_mode", "workspace-write")
            approval_policy = kwargs.get("approval_policy", "never")
            network_mode = kwargs.get("network_mode", "restricted")
            break_glass_expires_at = kwargs.get("break_glass_expires_at", "2026-04-20T12:30:00Z")
        return Session()

    async def restart_workspace(self, workspace_name: str, workspace_path: str, reason: str = "manual_restart") -> None:
        if self.restart_error is not None:
            raise self.restart_error
        self.restarts.append((workspace_name, workspace_path, reason))

    async def stop_workspace(self, workspace_name: str) -> bool:
        self.stops.append(workspace_name)
        return False


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, int | None]] = []

    async def send_message(self, chat_id: int, text: str, thread_id: int | None = None, reply_markup=None, reply_to_message_id=None):
        self.messages.append((chat_id, text, thread_id))
        return {"message_id": len(self.messages)}


def make_config(tmp: str, admin_ids: set[int], *, bind_admin: bool = True, use_admin: bool = False, approvals_admin: bool = True) -> AppConfig:
    profiles = {
        "default": ExecutionProfile("default", "workspace-write", "never", "restricted", "default", False),
        "ops": ExecutionProfile("ops", "workspace-write", "on-request", "restricted", "ops", True),
        "break-glass": ExecutionProfile("break-glass", "danger-full-access", "never", "enabled", "break-glass", True),
    }
    return AppConfig(
        bot_name="test",
        telegram_api_base="https://api.telegram.org",
        telegram_token="token",
        telegram_admin_ids=admin_ids,
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
        default_network_mode="restricted",
        session_idle_ttl_seconds=60,
        command_timeout_seconds=60,
        process_kill_grace_seconds=1,
        max_parallel_processes=1,
        max_queue_per_workspace=1,
        max_active_workspaces=4,
        per_user_rate_limit_window_seconds=10,
        per_user_rate_limit_max_messages=5,
        trusted_admin_only_bind=bind_admin,
        allowed_roots=[Path(tmp)],
        project_alias_roots=[Path(tmp)],
        workspace_defaults={"server-ops": tmp},
        workspace_profile_defaults={},
        execution_profiles=profiles,
        command_rule_groups={"default": ("workspace-safe",), "ops": ("workspace-safe",), "break-glass": ("workspace-safe",)},
        admin_only=AdminOnlySettings(
            bind=bind_admin,
            use=use_admin,
            execmode=True,
            approvals=approvals_admin,
            break_glass=True,
            command_rule_overrides=True,
        ),
        break_glass_ttl_seconds=1800,
        telegram=TelegramSettings(True, True, True),
        response_ux=default_response_ux_settings(),
    )


class AppSessionControlTests(unittest.IsolatedAsyncioTestCase):
    def _preflight_error(self, workspace_name: str, workspace_path: str) -> WorkspacePreflightError:
        return WorkspacePreflightError(
            WorkspacePreflightResult(
                workspace_name=workspace_name,
                requested_path=workspace_path,
                canonical_path=workspace_path,
                codex_dir=f"{workspace_path}/.codex",
                diagnostics=(PreflightDiagnostic("write_access", False, "Workspace is not writable."),),
            )
        )

    async def test_bind_denied_for_non_admin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            config = make_config(tmp, admin_ids=set(), bind_admin=True)
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            app = GatewayApp(config, store, FakeSessions(), telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._bind(ChatScope(chat_id=1, thread_id=None), 100, 1, None, ["demo", str(workspace)])

            self.assertEqual(telegram.messages[-1][1], "Admin only.")

    async def test_use_denied_when_configured_admin_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            config = make_config(tmp, admin_ids=set(), use_admin=True)
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            store.upsert_workspace("demo", str(workspace))
            telegram = FakeTelegram()
            app = GatewayApp(config, store, FakeSessions(), telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._use(ChatScope(chat_id=1, thread_id=None), 100, 1, None, ["demo"])

            self.assertEqual(telegram.messages[-1][1], "Admin only.")

    async def test_approvals_change_denied_for_non_admin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, admin_ids=set(), approvals_admin=True)
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            app = GatewayApp(config, store, FakeSessions(), telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._set_approvals(ChatScope(chat_id=1, thread_id=None), 100, 1, None, ["untrusted"])

            self.assertEqual(telegram.messages[-1][1], "Admin only.")

    async def test_approvals_change_allowed_for_admin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, admin_ids={7}, approvals_admin=True)
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            sessions = FakeSessions()
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._set_approvals(ChatScope(chat_id=1, thread_id=None), 7, 1, None, ["untrusted"])

            self.assertEqual(telegram.messages[-1][1], "Approvals set to untrusted. A fresh session will be used for the next run.")
            self.assertEqual(sessions.policy_changes[-1]["approval_policy"], "untrusted")

    async def test_session_profile_uses_alias_and_policy_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, admin_ids={7}, approvals_admin=True)
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            sessions = FakeSessions()
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._session_command(ChatScope(chat_id=1, thread_id=None), 7, 1, None, ["profile", "bg"])

            self.assertEqual(sessions.policy_changes[-1]["profile_name"], "break-glass")
            self.assertIn("Break-glass enabled until", telegram.messages[-1][1])

    async def test_session_restart_calls_session_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, admin_ids={7})
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            sessions = FakeSessions()
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._session_command(ChatScope(chat_id=1, thread_id=None), 7, 1, None, ["restart"])

            self.assertEqual(sessions.restarts[-1][2], "session_restart")
            self.assertEqual(telegram.messages[-1][1], "Session restarted for server-ops.")

    async def test_session_restart_reports_preflight_failure_to_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            config = make_config(tmp, admin_ids={7})
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            store.upsert_workspace("demo", str(workspace))
            store.bind_scope(ChatScope(chat_id=1, thread_id=None), "demo")
            telegram = FakeTelegram()
            sessions = FakeSessions(restart_error=self._preflight_error("demo", str(workspace)))
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._session_command(ChatScope(chat_id=1, thread_id=None), 7, 1, None, ["restart"])

            self.assertEqual(
                telegram.messages[-1][1],
                "Workspace preflight failed (write_access): Workspace is not writable.",
            )

    async def test_reset_session_reports_preflight_failure_to_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            config = make_config(tmp, admin_ids={7})
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            store.upsert_workspace("demo", str(workspace))
            store.bind_scope(ChatScope(chat_id=1, thread_id=None), "demo")
            telegram = FakeTelegram()
            sessions = FakeSessions(restart_error=self._preflight_error("demo", str(workspace)))
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._reset_session(ChatScope(chat_id=1, thread_id=None), 7, 1, None)

            self.assertEqual(
                telegram.messages[-1][1],
                "Workspace preflight failed (write_access): Workspace is not writable.",
            )

    async def test_session_restart_hides_internal_workspace_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, admin_ids={7})
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            internal_name = "session:1:2:server-ops"
            store.upsert_workspace(internal_name, tmp)
            store.bind_scope(ChatScope(chat_id=1, thread_id=2), internal_name)
            telegram = FakeTelegram()
            sessions = FakeSessions()
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._session_command(ChatScope(chat_id=1, thread_id=2), 7, 1, 2, ["restart"])

            self.assertEqual(telegram.messages[-1][1], "Session restarted for server-ops.")

    async def test_reset_session_hides_internal_workspace_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, admin_ids={7})
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            internal_name = "session:1:2:server-ops"
            store.upsert_workspace(internal_name, tmp)
            store.bind_scope(ChatScope(chat_id=1, thread_id=2), internal_name)
            telegram = FakeTelegram()
            sessions = FakeSessions()
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._reset_session(ChatScope(chat_id=1, thread_id=2), 7, 1, 2)

            self.assertEqual(telegram.messages[-1][1], "Session reset for server-ops.")

    async def test_handle_command_legacy_execmode_routes_to_policy_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, admin_ids={7})
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            sessions = FakeSessions()
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._handle_command(ChatScope(chat_id=1, thread_id=None), 7, 1, None, None, "/execmode readonly")

            self.assertEqual(sessions.policy_changes[-1]["sandbox_mode"], "read-only")
            self.assertIn("A fresh session will be used for the next run.", telegram.messages[-1][1])

    async def test_handle_command_legacy_approvals_routes_to_policy_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, admin_ids={7})
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            sessions = FakeSessions()
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"), policy_resolver=ExecutionPolicyResolver(config))

            await app._handle_command(ChatScope(chat_id=1, thread_id=None), 7, 1, None, None, "/approvals untrusted")

            self.assertEqual(sessions.policy_changes[-1]["approval_policy"], "untrusted")
            self.assertIn("A fresh session will be used for the next run.", telegram.messages[-1][1])


if __name__ == "__main__":
    unittest.main()
