from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from pathlib import Path

from codex_telegram_gateway.config import AdminOnlySettings, AppConfig, ExecutionProfile, TelegramSettings
from codex_telegram_gateway.execution_policy import ExecutionPolicyResolver
from codex_telegram_gateway.models import CodexRunResult
from codex_telegram_gateway.session_manager import SessionManager
from codex_telegram_gateway.workspace_preflight import (
    PreflightDiagnostic,
    WorkspacePreflightChecker,
    WorkspacePreflightError,
    WorkspacePreflightResult,
)
from codex_telegram_gateway.workspace_store import WorkspaceStore


class FakeProcess:
    def __init__(self) -> None:
        self.returncode = 0


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, *, workspace_path: str, prompt: str, session_id: str | None, model: str | None, policy, on_event=None, on_process=None):
        self.calls.append(prompt)
        if on_process:
            on_process(FakeProcess())
        if on_event:
            await on_event({"type": "message.delta", "text": f"reply:{prompt}"})
        return (
            CodexRunResult(
                ok=True,
                final_text=f"reply:{prompt}",
                session_id="session-1",
                exit_code=0,
                duration_seconds=0.1,
                errors=[],
                raw_events=[],
            ),
            FakeProcess(),
        )

    async def _terminate(self, proc) -> None:
        proc.returncode = -15
        return None


class FakePreflightChecker(WorkspacePreflightChecker):
    def __init__(self, should_pass: bool, allowed_root: Path) -> None:
        self.should_pass = should_pass
        self.calls: list[tuple[str, str]] = []
        super().__init__([allowed_root])

    def run(self, workspace_name: str, workspace_path: str):
        self.calls.append((workspace_name, workspace_path))
        if self.should_pass:
            return super().run(workspace_name, workspace_path)
        return WorkspacePreflightResult(
            workspace_name=workspace_name,
            requested_path=workspace_path,
            canonical_path=str(Path(workspace_path).resolve()),
            codex_dir=str(Path(workspace_path) / ".codex"),
            diagnostics=(PreflightDiagnostic("write_access", False, "Workspace is not writable."),),
        )


class SessionManagerTests(unittest.IsolatedAsyncioTestCase):
    def _resolver(self) -> ExecutionPolicyResolver:
        profiles = {
            "default": ExecutionProfile("default", "workspace-write", "never", "restricted", "default", False),
            "ops": ExecutionProfile("ops", "workspace-write", "on-request", "restricted", "ops", True),
            "break-glass": ExecutionProfile("break-glass", "danger-full-access", "never", "enabled", "break-glass", True),
        }
        config = AppConfig(
            bot_name="test",
            telegram_api_base="https://api.telegram.org",
            telegram_token="token",
            telegram_admin_ids={1},
            poll_timeout_seconds=10,
            poll_retry_delay_seconds=1,
            telegram_message_chunk=3900,
            stream_edit_interval_seconds=2.0,
            status_port=8085,
            sqlite_path=Path("/tmp/gateway.sqlite3"),
            runtime_dir=Path("/tmp/runtime"),
            log_dir=Path("/tmp/log"),
            codex_bin="/bin/true",
            codex_auth_source_home=None,
            default_workspace_name="demo",
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
            trusted_admin_only_bind=True,
            allowed_roots=[Path("/srv/projects")],
            project_alias_roots=[Path("/srv/projects")],
            workspace_defaults={"demo": "/srv/projects/demo"},
            workspace_profile_defaults={},
            execution_profiles=profiles,
            command_rule_groups={"default": ("workspace-safe",), "ops": ("workspace-safe",), "break-glass": ("workspace-safe",)},
            admin_only=AdminOnlySettings(True, False, True, True, True, True),
            break_glass_ttl_seconds=1800,
            telegram=TelegramSettings(True, True, True),
        )
        return ExecutionPolicyResolver(config)

    async def test_execute_updates_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"demo": "/srv/projects/demo"}, None, "workspace-write", "never")
            store.initialize()
            adapter = FakeAdapter()
            manager = SessionManager(store, adapter, logging.getLogger("test"), 1.0, 3600, 4, 1, 4)
            seen: list[str] = []

            async def on_event(event):
                seen.append(event["text"])

            result = await manager.execute("demo", "/srv/projects/demo", 1, "hello", on_event)
            self.assertTrue(result.ok)
            self.assertEqual(seen, ["reply:hello"])
            session = store.get_session("demo")
            self.assertEqual(session.session_id, "session-1")
            self.assertEqual(session.busy_state, "idle")
            self.assertEqual(session.last_stop_reason, "completed")
            self.assertEqual(adapter.calls, ["hello"])

    async def test_stop_workspace_records_stop_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"demo": "/srv/projects/demo"}, None, "workspace-write", "never")
            store.initialize()
            adapter = FakeAdapter()
            manager = SessionManager(store, adapter, logging.getLogger("test"), 1.0, 3600, 4, 1, 4)
            runtime = manager.get_runtime("demo", "/srv/projects/demo")
            runtime.current_process = FakeProcess()

            stopped = await manager.stop_workspace("demo")

            self.assertTrue(stopped)
            session = store.get_session("demo")
            self.assertEqual(session.last_stop_reason, "manual_stop")
            self.assertEqual(session.busy_state, "idle")

    async def test_execute_expires_break_glass_before_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"demo": "/srv/projects/demo"}, None, "workspace-write", "never")
            store.initialize()
            store.update_execution_policy("demo", break_glass_expires_at="2000-01-01T00:00:00Z")
            adapter = FakeAdapter()
            manager = SessionManager(store, adapter, logging.getLogger("test"), 1.0, 3600, 4, 1, 4, policy_resolver=self._resolver())

            async def on_event(event):
                return None

            await manager.execute("demo", "/srv/projects/demo", 1, "hello", on_event)

            session = store.get_session("demo")
            self.assertIsNone(session.break_glass_expires_at)

    async def test_apply_policy_change_resets_session_for_new_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"demo": "/srv/projects/demo"}, None, "workspace-write", "never")
            store.initialize()
            store.update_session("demo", session_id="session-1")
            adapter = FakeAdapter()
            manager = SessionManager(store, adapter, logging.getLogger("test"), 1.0, 3600, 4, 1, 4, policy_resolver=self._resolver())

            session = await manager.apply_policy_change("demo", "/srv/projects/demo", profile_name="ops")

            self.assertIsNone(session.session_id)
            self.assertEqual(session.profile_name, "ops")
            self.assertEqual(session.execution_policy.override_scope, "durable-override")
            self.assertIsNotNone(session.last_restart_at)

    async def test_apply_policy_change_enables_break_glass_without_durable_profile_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "demo"
            workspace.mkdir()
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"demo": str(workspace)}, None, "workspace-write", "never")
            store.initialize()
            store.update_session("demo", session_id="session-1")
            adapter = FakeAdapter()
            manager = SessionManager(store, adapter, logging.getLogger("test"), 1.0, 3600, 4, 1, 4, policy_resolver=self._resolver())

            session = await manager.apply_policy_change("demo", str(workspace), profile_name="break-glass")

            self.assertIsNone(session.session_id)
            self.assertEqual(session.profile_name, "default")
            self.assertEqual(session.execution_policy.override_scope, "profile-default")
            self.assertIsNotNone(session.break_glass_expires_at)
            resolved = self._resolver().resolve(
                workspace_name="demo",
                workspace_path=str(workspace),
                user_id=1,
                stored_policy=session.execution_policy,
            )
            self.assertTrue(resolved.break_glass_active)
            self.assertEqual(resolved.profile_name, "break-glass")

    async def test_apply_policy_change_rejects_busy_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"demo": "/srv/projects/demo"}, None, "workspace-write", "never")
            store.initialize()
            adapter = FakeAdapter()
            manager = SessionManager(store, adapter, logging.getLogger("test"), 1.0, 3600, 4, 1, 4, policy_resolver=self._resolver())
            runtime = manager.get_runtime("demo", "/srv/projects/demo")
            runtime.current_process = FakeProcess()

            with self.assertRaises(RuntimeError):
                await manager.apply_policy_change("demo", "/srv/projects/demo", profile_name="ops")

    async def test_restart_workspace_runs_preflight_before_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "demo"
            workspace.mkdir()
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"demo": str(workspace)}, None, "workspace-write", "never")
            store.initialize()
            store.update_session("demo", session_id="session-1")
            adapter = FakeAdapter()
            preflight_checker = FakePreflightChecker(True, Path(tmp))
            manager = SessionManager(
                store,
                adapter,
                logging.getLogger("test"),
                1.0,
                3600,
                4,
                1,
                4,
                preflight_checker=preflight_checker,
            )

            await manager.restart_workspace("demo", str(workspace), reason="session_restart")

            session = store.get_session("demo")
            self.assertEqual(preflight_checker.calls, [("demo", str(workspace))])
            self.assertIsNone(session.session_id)

    async def test_restart_workspace_preserves_session_when_preflight_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "demo"
            workspace.mkdir()
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"demo": str(workspace)}, None, "workspace-write", "never")
            store.initialize()
            store.update_session("demo", session_id="session-1")
            adapter = FakeAdapter()
            preflight_checker = FakePreflightChecker(False, Path(tmp))
            manager = SessionManager(
                store,
                adapter,
                logging.getLogger("test"),
                1.0,
                3600,
                4,
                1,
                4,
                preflight_checker=preflight_checker,
            )

            with self.assertRaises(WorkspacePreflightError):
                await manager.restart_workspace("demo", str(workspace), reason="session_restart")

            session = store.get_session("demo")
            self.assertEqual(session.session_id, "session-1")


if __name__ == "__main__":
    unittest.main()
