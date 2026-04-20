from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from pathlib import Path

from codex_telegram_gateway.config import (
    AdminOnlySettings,
    AppConfig,
    ExecutionProfile,
    TelegramSettings,
    default_response_ux_settings,
)
from codex_telegram_gateway.models import CodexRunResult, RunEvent, TelegramRequestIdentity, TelegramResponseContext, TelegramResponseTarget
from codex_telegram_gateway.response_ux import ResponseUxCoordinator


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.reactions: list[tuple[int, int]] = []
        self.typing_calls: list[tuple[int, int | None]] = []

    def capabilities_for(self, chat_id: int):
        class Capabilities:
            message_reactions = True
            chat_actions = True
            message_edits = True

        return Capabilities()

    async def send_message(self, chat_id: int, text: str, thread_id: int | None = None, reply_to_message_id: int | None = None, reply_markup=None):
        self.messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "thread_id": thread_id,
                "reply_to_message_id": reply_to_message_id,
                "reply_markup": reply_markup,
                "message_id": len(self.messages) + 1,
            }
        )
        return {"message_id": len(self.messages)}

    async def send_message_reaction(self, chat_id: int, message_id: int, emoji: str = "👍", *, is_big: bool = False) -> bool:
        self.reactions.append((chat_id, message_id))
        return True

    async def send_typing_action(self, chat_id: int, thread_id: int | None = None) -> bool:
        self.typing_calls.append((chat_id, thread_id))
        return True

    async def send_or_edit_message(self, *, chat_id: int, text: str, thread_id: int | None = None, reply_to_message_id: int | None = None, reply_markup=None, edit_message_id: int | None = None):
        mode = "edit" if edit_message_id is not None else "send"
        message = await self.send_message(chat_id, text, thread_id=thread_id, reply_to_message_id=reply_to_message_id, reply_markup=reply_markup)

        class Result:
            def __init__(self, mode: str, message: dict) -> None:
                self.mode = mode
                self.message = message

        return Result(mode, message)


def make_config(tmp: str) -> AppConfig:
    profiles = {"default": ExecutionProfile("default", "workspace-write", "never", "restricted", "default", False)}
    return AppConfig(
        bot_name="test",
        telegram_api_base="https://api.telegram.org",
        telegram_token="token",
        telegram_admin_ids={1},
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


class ResponseUxTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_policy_sends_single_final_message_without_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            telegram = FakeTelegram()
            coordinator = ResponseUxCoordinator(config, telegram, logging.getLogger("test"))
            context = TelegramResponseContext(
                identity=TelegramRequestIdentity(chat_id=-100, thread_id=55, message_id=42),
                target=TelegramResponseTarget(chat_id=-100, thread_id=55, reply_to_message_id=42),
                workspace_name="demo",
                workspace_path=tmp,
                chat_type="supergroup",
                user_id=7,
                prompt="hello",
                policy=config.response_ux.resolve_policy(chat_type="supergroup", thread_id=55),
            )

            async def execute_run(on_event):
                await on_event(RunEvent(kind="text_delta", text="partial", raw_type="message.delta"))
                return CodexRunResult(True, "done", "session-1", 0, 0.1, [], [])

            await coordinator.run(context, execute_run)

            self.assertEqual(len(telegram.messages), 1)
            self.assertIn("[demo] done in 0.1s", telegram.messages[0]["text"])

    async def test_duplicate_request_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            telegram = FakeTelegram()
            coordinator = ResponseUxCoordinator(config, telegram, logging.getLogger("test"))
            context = TelegramResponseContext(
                identity=TelegramRequestIdentity(chat_id=1, thread_id=None, message_id=42),
                target=TelegramResponseTarget(chat_id=1, thread_id=None, reply_to_message_id=42),
                workspace_name="demo",
                workspace_path=tmp,
                chat_type="private",
                user_id=7,
                prompt="hello",
                policy=config.response_ux.resolve_policy(chat_type="private", thread_id=None),
            )
            gate = asyncio.Event()

            async def execute_run(_on_event):
                await gate.wait()
                return CodexRunResult(True, "done", "session-1", 0, 0.1, [], [])

            first = asyncio.create_task(coordinator.run(context, execute_run))
            await asyncio.sleep(0)
            await coordinator.run(context, execute_run)
            gate.set()
            await first

            self.assertEqual(len(telegram.messages), 1)


if __name__ == "__main__":
    unittest.main()
