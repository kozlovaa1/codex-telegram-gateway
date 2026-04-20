from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from pathlib import Path

from codex_telegram_gateway.app import GatewayApp
from codex_telegram_gateway.codex_adapter import PolicyEnforcementError
from codex_telegram_gateway.config import (
    AdminOnlySettings,
    AppConfig,
    ExecutionProfile,
    TelegramSettings,
    default_response_ux_settings,
)
from codex_telegram_gateway.models import ChatScope, CodexRunResult
from codex_telegram_gateway.telegram_api import TelegramApiError
from codex_telegram_gateway.workspace_preflight import PreflightDiagnostic, WorkspacePreflightError, WorkspacePreflightResult
from codex_telegram_gateway.workspace_store import WorkspaceStore


class FakeTelegram:
    def __init__(self, *, fail_edits: bool = False) -> None:
        self.fail_edits = fail_edits
        self.messages: list[dict[str, object]] = []
        self.edits: list[dict[str, object]] = []
        self.reactions: list[tuple[int, int, str]] = []
        self.typing_calls: list[tuple[int, int | None]] = []

    def capabilities_for(self, chat_id: int):
        class Capabilities:
            message_reactions = True
            chat_actions = True
            message_edits = True

        return Capabilities()

    async def send_message(
        self,
        chat_id: int,
        text: str,
        thread_id: int | None = None,
        reply_markup=None,
        reply_to_message_id: int | None = None,
    ) -> dict:
        message = {
            "chat_id": chat_id,
            "text": text,
            "thread_id": thread_id,
            "reply_markup": reply_markup,
            "reply_to_message_id": reply_to_message_id,
            "message_id": len(self.messages) + 1,
        }
        self.messages.append(message)
        return {"message_id": message["message_id"]}

    async def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup=None) -> dict:
        if self.fail_edits:
            raise TelegramApiError(
                "edit failed",
                method="editMessageText",
                classification="edit_conflict",
                fallback_allowed=True,
                final_delivery_risk=False,
            )
        edit = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "reply_markup": reply_markup,
        }
        self.edits.append(edit)
        return {"message_id": message_id}

    async def send_message_reaction(self, chat_id: int, message_id: int, emoji: str = "👍", *, is_big: bool = False) -> bool:
        self.reactions.append((chat_id, message_id, emoji))
        return True

    async def send_typing_action(self, chat_id: int, thread_id: int | None = None) -> bool:
        self.typing_calls.append((chat_id, thread_id))
        return True


class FakeSessions:
    def __init__(self, *, result: CodexRunResult | None = None, error: Exception | None = None, events: list[dict] | None = None) -> None:
        self.result = result or CodexRunResult(
            ok=True,
            final_text="done",
            session_id="session-1",
            exit_code=0,
            duration_seconds=0.1,
            errors=[],
            raw_events=[],
        )
        self.error = error
        self.events = events or []
        self.calls: list[tuple[str, str, int, str]] = []

    def runtime_snapshot(self):
        return []

    async def execute(self, workspace_name: str, workspace_path: str, user_id: int, prompt: str, stream_callback):
        self.calls.append((workspace_name, workspace_path, user_id, prompt))
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        for event in self.events:
            await stream_callback(event)
        return self.result


def make_config(tmp: str, *, interval: float = 2.0) -> AppConfig:
    profiles = {
        "default": ExecutionProfile("default", "workspace-write", "never", "restricted", "default", False),
    }
    return AppConfig(
        bot_name="test",
        telegram_api_base="https://api.telegram.org",
        telegram_token="token",
        telegram_admin_ids={1},
        poll_timeout_seconds=10,
        poll_retry_delay_seconds=1,
        telegram_message_chunk=3900,
        stream_edit_interval_seconds=interval,
        status_port=8085,
        sqlite_path=Path(tmp) / "db.sqlite3",
        runtime_dir=Path(tmp) / "runtime",
        log_dir=Path(tmp) / "log",
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
        allowed_roots=[Path(tmp)],
        project_alias_roots=[Path(tmp)],
        workspace_defaults={"demo": tmp},
        workspace_profile_defaults={},
        execution_profiles=profiles,
        command_rule_groups={"default": ("workspace-safe",)},
        admin_only=AdminOnlySettings(True, False, True, True, True, True),
        break_glass_ttl_seconds=1800,
        telegram=TelegramSettings(True, True, True),
        response_ux=default_response_ux_settings(),
    )


class AppPromptFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_prompt_sends_initial_ack_and_final_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            sessions = FakeSessions(events=[{"type": "message.delta", "text": "partial"}])
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"))

            await app._handle_prompt(ChatScope(chat_id=1, thread_id=None), 7, 1, None, 42, "hello", chat_type="private")

            self.assertEqual(telegram.reactions, [(1, 42, "👍")])
            self.assertGreaterEqual(len(telegram.typing_calls), 1)
            self.assertEqual(telegram.messages[0]["text"], "[demo] running\n\npartial")
            self.assertTrue(any(edit["text"].startswith("[demo] done in 0.1s") for edit in telegram.edits))

    async def test_handle_prompt_throttles_stream_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp, interval=3600.0)
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            sessions = FakeSessions(
                events=[
                    {"type": "message.delta", "text": "first"},
                    {"type": "message.delta", "text": "second"},
                ]
            )
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"))

            await app._handle_prompt(ChatScope(chat_id=1, thread_id=None), 7, 1, None, 42, "hello", chat_type="private")

            running_messages = [message for message in telegram.messages if message["text"].startswith("[demo] running")]
            self.assertEqual(len(running_messages), 1)

    async def test_handle_prompt_reports_preflight_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            error = WorkspacePreflightError(
                WorkspacePreflightResult(
                    workspace_name="demo",
                    requested_path=tmp,
                    canonical_path=tmp,
                    codex_dir=f"{tmp}/.codex",
                    diagnostics=(PreflightDiagnostic("write_access", False, "Workspace is not writable."),),
                )
            )
            app = GatewayApp(config, store, FakeSessions(error=error), telegram, logging.getLogger("test"))

            await app._handle_prompt(ChatScope(chat_id=1, thread_id=None), 7, 1, None, 42, "hello", chat_type="private")

            self.assertEqual(telegram.messages[-1]["text"], "[demo] Workspace preflight failed (write_access): Workspace is not writable.")

    async def test_handle_prompt_reports_policy_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            app = GatewayApp(
                config,
                store,
                FakeSessions(error=PolicyEnforcementError("approval denied")),
                telegram,
                logging.getLogger("test"),
            )

            await app._handle_prompt(ChatScope(chat_id=1, thread_id=None), 7, 1, None, 42, "hello", chat_type="private")

            self.assertEqual(telegram.messages[-1]["text"], "[demo] approval denied")

    async def test_handle_prompt_reports_internal_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram()
            app = GatewayApp(config, store, FakeSessions(error=RuntimeError("boom")), telegram, logging.getLogger("test"))

            await app._handle_prompt(ChatScope(chat_id=1, thread_id=None), 7, 1, None, 42, "hello", chat_type="private")

            self.assertEqual(telegram.messages[-1]["text"], "[demo] internal error")

    async def test_handle_prompt_falls_back_to_new_message_when_edit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            store = WorkspaceStore(config.sqlite_path, config.workspace_defaults, None, "workspace-write", "never")
            store.initialize()
            telegram = FakeTelegram(fail_edits=True)
            sessions = FakeSessions(
                result=CodexRunResult(True, "done", "session-1", 0, 0.1, [], []),
                events=[{"type": "message.delta", "text": "partial"}],
            )
            app = GatewayApp(config, store, sessions, telegram, logging.getLogger("test"))

            await app._handle_prompt(ChatScope(chat_id=1, thread_id=None), 7, 1, None, 42, "hello", chat_type="private")

            self.assertEqual(len(telegram.messages), 2)
            self.assertEqual(telegram.messages[0]["text"], "[demo] running\n\npartial")
            self.assertEqual(telegram.messages[1]["text"], "[demo] done in 0.1s\nSession: session-1\n\ndone")


if __name__ == "__main__":
    unittest.main()
