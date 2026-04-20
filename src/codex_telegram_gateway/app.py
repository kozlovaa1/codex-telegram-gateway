from __future__ import annotations

import asyncio
import json
import logging
import signal
import textwrap
from datetime import UTC, datetime

from .config import AppConfig
from .execution_policy import ExecutionPolicyResolver, PolicyAuthorizationError
from .logging_utils import log_extra
from .models import ChatScope, TelegramRequestIdentity, TelegramResponseContext, TelegramResponseTarget
from .path_security import PathSecurityError, resolve_workspace_path
from .rate_limit import RateLimiter
from .response_ux import ResponseUxCoordinator
from .session_manager import SessionManager
from .telegram_api import TelegramApi
from .workspace_preflight import WorkspacePreflightError
from .workspace_store import WorkspaceStore


INLINE_KEYBOARD = json.dumps(
    {
        "inline_keyboard": [
            [
                {"text": "Status", "callback_data": "status"},
                {"text": "Where", "callback_data": "where"},
                {"text": "Reset Session", "callback_data": "resetsession"},
            ]
        ]
    }
)

SESSION_WORKSPACE_PREFIX = "session:"


def make_session_workspace_name(base_name: str, chat_id: int, thread_id: int) -> str:
    return f"{SESSION_WORKSPACE_PREFIX}{chat_id}:{thread_id}:{base_name}"


def display_workspace_name(name: str) -> str:
    if not name.startswith(SESSION_WORKSPACE_PREFIX):
        return name
    parts = name.split(":", 3)
    return parts[3] if len(parts) == 4 else name


def is_internal_session_workspace(name: str) -> bool:
    return name.startswith(SESSION_WORKSPACE_PREFIX)


def supports_topic_creation(chat_type: str | None, is_forum: bool) -> bool:
    return (chat_type == "supergroup" and is_forum) or chat_type == "private"


class GatewayApp:
    def __init__(
        self,
        config: AppConfig,
        store: WorkspaceStore,
        sessions: SessionManager,
        telegram: TelegramApi,
        logger: logging.Logger,
        policy_resolver: ExecutionPolicyResolver | None = None,
        response_ux: ResponseUxCoordinator | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.sessions = sessions
        self.telegram = telegram
        self.logger = logger
        self.policy_resolver = policy_resolver
        self.response_ux = response_ux or ResponseUxCoordinator(config, telegram, logger)
        self.rate_limiter = RateLimiter(
            config.per_user_rate_limit_window_seconds,
            config.per_user_rate_limit_max_messages,
        )
        self._offset: int | None = None
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._stop_event.set)
        while not self._stop_event.is_set():
            try:
                updates = await self.telegram.get_updates(self._offset, self.config.poll_timeout_seconds)
            except TelegramApiError:
                self.logger.exception("telegram.poll.failed")
                await asyncio.sleep(self.config.poll_retry_delay_seconds)
                continue
            for update in updates:
                self._offset = update["update_id"] + 1
                asyncio.create_task(self.handle_update(update))

    async def handle_update(self, update: dict) -> None:
        log_extra(self.logger, "telegram.update", update_id=update.get("update_id"))
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
            return
        message = update.get("message") or update.get("edited_message")
        if not message or "text" not in message:
            return
        await self._handle_message(message)

    async def _handle_callback(self, callback: dict) -> None:
        data = callback.get("data", "")
        message = callback.get("message") or {}
        scope = ChatScope(
            chat_id=message.get("chat", {}).get("id"),
            thread_id=message.get("message_thread_id"),
        )
        if data == "status":
            await self.telegram.answer_callback_query(callback["id"], "Status")
            await self._send_status(scope, message["chat"]["id"], message.get("message_thread_id"), user_id=callback["from"]["id"])
        elif data == "where":
            await self.telegram.answer_callback_query(callback["id"], "Workspace")
            await self._send_where(scope, message["chat"]["id"], message.get("message_thread_id"))
        elif data == "resetsession":
            await self.telegram.answer_callback_query(callback["id"], "Session reset")
            await self._reset_session(scope, callback["from"]["id"], message["chat"]["id"], message.get("message_thread_id"))

    async def _handle_message(self, message: dict) -> None:
        chat = message["chat"]
        chat_type = chat.get("type")
        if chat_type == "private" and not self.config.telegram.allow_private_chats:
            return
        if chat_type in {"group", "supergroup"} and not self.config.telegram.allow_group_chats:
            return
        user = message.get("from") or {}
        user_id = int(user.get("id", 0))
        allowed, retry_after = self.rate_limiter.allow(user_id)
        if not allowed:
            await self.telegram.send_message(chat["id"], f"Rate limit exceeded. Retry in about {retry_after}s.", message.get("message_thread_id"))
            return
        scope = ChatScope(chat_id=int(chat["id"]), thread_id=message.get("message_thread_id"))
        text = str(message.get("text", "")).strip()
        if text.startswith("/"):
            await self._handle_command(
                scope,
                user_id,
                chat["id"],
                message.get("message_thread_id"),
                message.get("message_id"),
                text,
                chat_type=chat_type,
                is_forum=bool(chat.get("is_forum")),
            )
            return
        await self._handle_prompt(
            scope,
            user_id,
            chat["id"],
            message.get("message_thread_id"),
            message.get("message_id"),
            text,
            chat_type=chat_type,
        )

    def _is_admin(self, user_id: int) -> bool:
        return user_id in self.config.telegram_admin_ids

    def _workspace_from_scope(self, scope: ChatScope) -> tuple[str, str] | None:
        binding = self.store.get_binding(scope)
        if binding:
            workspace = self.store.get_workspace(binding.workspace_name)
            if workspace:
                return workspace.name, workspace.path
        if not self.config.default_workspace_name:
            return None
        workspace = self.store.get_workspace(self.config.default_workspace_name)
        if not workspace:
            return None
        return workspace.name, workspace.path

    def _dynamic_project_workspaces(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for root in self.config.project_alias_roots:
            if not root.exists():
                continue
            for child in sorted(root.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    result[f"project:{child.name}"] = str(child.resolve())
        return result

    async def _handle_command(
        self,
        scope: ChatScope,
        user_id: int,
        chat_id: int,
        thread_id: int | None,
        message_id: int | None,
        text: str,
        chat_type: str | None = None,
        is_forum: bool = False,
    ) -> None:
        parts = text.split()
        command = parts[0].split("@", 1)[0].lower()
        args = parts[1:]
        if command == "/start":
            resolved = self._workspace_from_scope(scope)
            if resolved:
                workspace_name, workspace_path = resolved
                text = (
                    "Codex Telegram Gateway is ready.\n"
                    f"Current workspace: {display_workspace_name(workspace_name)} -> {workspace_path}\n"
                    "Use /help, /where and /workspaces to inspect or change it."
                )
            else:
                text = "Codex Telegram Gateway is ready.\nUse /help and /workspaces to bind a workspace."
            await self.telegram.send_message(chat_id, text, thread_id, reply_markup=INLINE_KEYBOARD)
        elif command == "/help":
            await self.telegram.send_message(chat_id, self._help_text(), thread_id)
        elif command == "/status":
            await self._send_status(scope, chat_id, thread_id)
        elif command in {"/where", "/pwd"}:
            await self._send_where(scope, chat_id, thread_id)
        elif command == "/workspaces":
            await self._send_workspaces(chat_id, thread_id)
        elif command == "/bind":
            await self._bind(scope, user_id, chat_id, thread_id, args)
        elif command == "/use":
            await self._use(scope, user_id, chat_id, thread_id, args, chat_type=chat_type, is_forum=is_forum)
        elif command == "/session":
            await self._session_command(scope, user_id, chat_id, thread_id, args)
        elif command in {"/newsession", "/resetsession"}:
            await self._reset_session(scope, user_id, chat_id, thread_id)
        elif command == "/stop":
            await self._stop(scope, chat_id, thread_id)
        elif command == "/model":
            await self._set_model(scope, chat_id, thread_id, args)
        elif command == "/execmode":
            await self._set_execmode(scope, user_id, chat_id, thread_id, args)
        elif command == "/approvals":
            await self._set_approvals(scope, user_id, chat_id, thread_id, args)
        elif command == "/debugstatus":
            if not self._is_admin(user_id):
                await self.telegram.send_message(chat_id, "Admin only.", thread_id)
            else:
                await self._debug_status(chat_id, thread_id)
        else:
            await self.telegram.send_message(chat_id, "Unknown command. Use /help.", thread_id)

    def _help_text(self) -> str:
        return textwrap.dedent(
            """
            Commands:
            /start
            /help
            /status
            /where
            /workspaces
            /bind <name> <path>   admin only
            /use <name>
            /session [show|profile <name>|restart|reset]
            /newsession
            /resetsession
            /stop
            /pwd
            /execmode [readonly|workspace-write]
            /approvals [never|untrusted]
            /model [name]
            /debugstatus   admin only
            """
        ).strip()

    async def _bind(self, scope: ChatScope, user_id: int, chat_id: int, thread_id: int | None, args: list[str]) -> None:
        if len(args) != 2:
            await self.telegram.send_message(chat_id, "Usage: /bind <name> <path>", thread_id)
            return
        name, raw_path = args
        try:
            resolved = resolve_workspace_path(raw_path, self.config.allowed_roots)
        except PathSecurityError as exc:
            await self.telegram.send_message(chat_id, f"Bind rejected: {exc}", thread_id)
            return
        try:
            self._authorize_command("bind", user_id, workspace_name=name, workspace_path=str(resolved))
        except PolicyAuthorizationError as exc:
            await self.telegram.send_message(chat_id, str(exc), thread_id)
            return
        self.store.upsert_workspace(name, str(resolved))
        self.store.bind_scope(scope, name)
        await self.telegram.send_message(chat_id, f"Bound to `{name}` -> {resolved}", thread_id)

    async def _use(self, scope: ChatScope, user_id: int, chat_id: int, thread_id: int | None, args: list[str], chat_type: str | None = None, is_forum: bool = False) -> None:
        if len(args) != 1:
            await self.telegram.send_message(chat_id, "Usage: /use <name>", thread_id)
            return
        available = {w.name: w.path for w in self.store.list_workspaces() if not is_internal_session_workspace(w.name)}
        available.update(self._dynamic_project_workspaces())
        name = args[0]
        path = available.get(name)
        if not path:
            await self.telegram.send_message(chat_id, f"Unknown workspace: {name}", thread_id)
            return
        try:
            self._authorize_command("use", user_id, workspace_name=name, workspace_path=path)
        except PolicyAuthorizationError as exc:
            await self.telegram.send_message(chat_id, str(exc), thread_id)
            return
        if supports_topic_creation(chat_type, is_forum):
            topic_title = f"{name} | {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}"
            try:
                created_topic = await self.telegram.create_forum_topic(chat_id, topic_title)
            except TelegramApiError as exc:
                await self.telegram.send_message(
                    chat_id,
                    f"Failed to create topic for workspace `{name}`: {exc}",
                    thread_id,
                )
                return
            new_thread_id = int(created_topic["message_thread_id"])
            session_workspace_name = make_session_workspace_name(name, chat_id, new_thread_id)
            self.store.upsert_workspace(session_workspace_name, path)
            self.store.bind_scope(ChatScope(chat_id=chat_id, thread_id=new_thread_id), session_workspace_name)
            self.store.update_session(session_workspace_name, session_id="", touch_last_used=False)
            await self.telegram.send_message(
                chat_id,
                f"Workspace: {name}\nPath: {path}\nBinding: explicit topic session",
                new_thread_id,
                reply_markup=INLINE_KEYBOARD,
            )
            await self.telegram.send_message(chat_id, f"Created topic `{topic_title}` for workspace `{name}`.", thread_id)
            return
        self.store.upsert_workspace(name, path)
        self.store.bind_scope(scope, name)
        await self.telegram.send_message(chat_id, f"Using workspace `{name}` -> {path}", thread_id)

    async def _send_workspaces(self, chat_id: int, thread_id: int | None) -> None:
        items = {w.name: w.path for w in self.store.list_workspaces() if not is_internal_session_workspace(w.name)}
        items.update(self._dynamic_project_workspaces())
        lines = [f"{name} -> {path}" for name, path in sorted(items.items())]
        await self.telegram.send_message(chat_id, "Available workspaces:\n" + "\n".join(lines[:80]), thread_id)

    async def _send_where(self, scope: ChatScope, chat_id: int, thread_id: int | None) -> None:
        resolved = self._workspace_from_scope(scope)
        if not resolved:
            await self.telegram.send_message(chat_id, "No workspace bound. Use /workspaces and /use <name>.", thread_id)
            return
        name, path = resolved
        binding = self.store.get_binding(scope)
        mode = "explicit" if binding else "default"
        await self.telegram.send_message(chat_id, f"Workspace: {display_workspace_name(name)}\nPath: {path}\nScope: {scope.key}\nBinding: {mode}", thread_id)

    async def _send_status(self, scope: ChatScope, chat_id: int, thread_id: int | None, user_id: int | None = None) -> None:
        resolved = self._workspace_from_scope(scope)
        if not resolved:
            await self.telegram.send_message(chat_id, "No workspace bound.", thread_id)
            return
        name, path = resolved
        session = self.store.get_session(name)
        resolved_policy = None
        if self.policy_resolver is not None:
            resolved_policy = self.policy_resolver.resolve(
                workspace_name=name,
                workspace_path=path,
                user_id=user_id or 0,
                stored_policy=session.execution_policy,
            )
        runtime = next((item for item in self.sessions.runtime_snapshot() if item["workspace"] == name), None)
        busy = bool(runtime and runtime["busy"])
        runtime_seconds = int(runtime["runtime_seconds"]) if runtime else 0
        active_profile = resolved_policy.profile_name if resolved_policy is not None else session.profile_name
        active_sandbox = resolved_policy.sandbox_mode if resolved_policy is not None else session.sandbox_mode
        active_approvals = resolved_policy.approval_policy if resolved_policy is not None else session.approval_policy
        active_network = resolved_policy.network_mode if resolved_policy is not None else session.network_mode
        rule_set = resolved_policy.command_rule_group if resolved_policy is not None else f"v{session.command_rule_set_version}"
        text = "\n".join(
            [
                f"Workspace: {display_workspace_name(name)}",
                f"Path: {path}",
                f"Profile: {active_profile}",
                f"Session: {session.session_id or 'new'}",
                f"Busy: {'yes' if busy else 'no'}",
                f"Runtime: {runtime_seconds}s" if busy else "Runtime: idle",
                f"Mode: {active_sandbox}",
                f"Approvals: {active_approvals}",
                f"Network: {active_network}",
                f"Rule set: {rule_set}",
                f"Model: {session.model or '(default)'}",
                f"Break-glass expires: {session.break_glass_expires_at or 'inactive'}",
                f"Last used: {session.last_used_at or 'never'}",
                f"Last restart: {session.last_restart_at or 'never'}",
            ]
        )
        await self.telegram.send_message(chat_id, text, thread_id, reply_markup=INLINE_KEYBOARD)

    async def _reset_session(self, scope: ChatScope, user_id: int, chat_id: int, thread_id: int | None) -> None:
        resolved = self._workspace_from_scope(scope)
        if not resolved:
            await self.telegram.send_message(chat_id, "No workspace bound.", thread_id)
            return
        name, path = resolved
        if await self.sessions.stop_workspace(name):
            log_extra(self.logger, "codex.run.stopped", workspace=name, by_user=user_id)
        try:
            await self.response_ux.cancel_scope(chat_id, thread_id, reason="session_reset")
            await self.sessions.restart_workspace(name, path, reason="session_reset")
        except WorkspacePreflightError as exc:
            self.logger.warning(
                "[FIX] session_reset_preflight_failed",
                extra={
                    "extra_fields": {
                        "workspace_name": name,
                        "workspace_path": path,
                        "reason": exc.result.user_message,
                    }
                },
            )
            await self.telegram.send_message(chat_id, exc.result.user_message, thread_id)
            return
        await self.telegram.send_message(chat_id, f"Session reset for {display_workspace_name(name)}.", thread_id)

    async def _stop(self, scope: ChatScope, chat_id: int, thread_id: int | None) -> None:
        resolved = self._workspace_from_scope(scope)
        if not resolved:
            await self.telegram.send_message(chat_id, "No workspace bound.", thread_id)
            return
        name, _ = resolved
        await self.response_ux.cancel_scope(chat_id, thread_id, reason="manual_stop")
        stopped = await self.sessions.stop_workspace(name)
        await self.telegram.send_message(chat_id, "Stopped active run." if stopped else "No active run.", thread_id)

    async def _set_model(self, scope: ChatScope, chat_id: int, thread_id: int | None, args: list[str]) -> None:
        resolved = self._workspace_from_scope(scope)
        if not resolved:
            await self.telegram.send_message(chat_id, "No workspace bound.", thread_id)
            return
        name, _ = resolved
        if not args:
            session = self.store.get_session(name)
            await self.telegram.send_message(chat_id, f"Model: {session.model or '(default)'}", thread_id)
            return
        model = args[0]
        self.store.update_session(name, model=model)
        await self.telegram.send_message(chat_id, f"Model set to {model}", thread_id)

    async def _set_execmode(self, scope: ChatScope, user_id: int, chat_id: int, thread_id: int | None, args: list[str]) -> None:
        resolved = self._workspace_from_scope(scope)
        if not resolved:
            await self.telegram.send_message(chat_id, "No workspace bound.", thread_id)
            return
        name, path = resolved
        if not args:
            session = self.store.get_session(name)
            await self.telegram.send_message(chat_id, f"Exec mode: {session.sandbox_mode}", thread_id)
            return
        mode = args[0]
        if mode not in {"read-only", "workspace-write", "readonly"}:
            await self.telegram.send_message(chat_id, "Allowed values: readonly, workspace-write", thread_id)
            return
        if mode == "readonly":
            mode = "read-only"
        try:
            self._authorize_command("execmode", user_id, workspace_name=name, requested_profile_name="default")
        except PolicyAuthorizationError as exc:
            await self.telegram.send_message(chat_id, str(exc), thread_id)
            return
        await self.sessions.apply_policy_change(name, path, sandbox_mode=mode, reason="execmode_change")
        await self.telegram.send_message(chat_id, f"Exec mode set to {mode}. A fresh session will be used for the next run.", thread_id)

    async def _set_approvals(self, scope: ChatScope, user_id: int, chat_id: int, thread_id: int | None, args: list[str]) -> None:
        resolved = self._workspace_from_scope(scope)
        if not resolved:
            await self.telegram.send_message(chat_id, "No workspace bound.", thread_id)
            return
        name, path = resolved
        if not args:
            session = self.store.get_session(name)
            await self.telegram.send_message(chat_id, f"Approvals: {session.approval_policy}", thread_id)
            return
        policy = args[0]
        if policy not in {"never", "untrusted"}:
            await self.telegram.send_message(chat_id, "Allowed values: never, untrusted", thread_id)
            return
        try:
            self._authorize_command("approvals", user_id, workspace_name=name, requested_approval_policy=policy)
        except PolicyAuthorizationError as exc:
            await self.telegram.send_message(chat_id, str(exc), thread_id)
            return
        await self.sessions.apply_policy_change(name, path, approval_policy=policy, reason="approval_policy_change")
        await self.telegram.send_message(chat_id, f"Approvals set to {policy}. A fresh session will be used for the next run.", thread_id)

    async def _session_command(self, scope: ChatScope, user_id: int, chat_id: int, thread_id: int | None, args: list[str]) -> None:
        if not args or args[0] == "show":
            await self._send_status(scope, chat_id, thread_id, user_id=user_id)
            return
        resolved = self._workspace_from_scope(scope)
        if not resolved:
            await self.telegram.send_message(chat_id, "No workspace bound.", thread_id)
            return
        name, path = resolved
        action = args[0].lower()
        if action == "restart":
            try:
                await self.response_ux.cancel_scope(chat_id, thread_id, reason="session_restart")
                await self.sessions.restart_workspace(name, path, reason="session_restart")
            except WorkspacePreflightError as exc:
                self.logger.warning(
                    "[FIX] session_restart_preflight_failed",
                    extra={
                        "extra_fields": {
                            "workspace_name": name,
                            "workspace_path": path,
                            "reason": exc.result.user_message,
                        }
                    },
                )
                await self.telegram.send_message(chat_id, exc.result.user_message, thread_id)
                return
            await self.telegram.send_message(chat_id, f"Session restarted for {display_workspace_name(name)}.", thread_id)
            return
        if action == "reset":
            await self._reset_session(scope, user_id, chat_id, thread_id)
            return
        if action == "profile":
            if len(args) != 2:
                await self.telegram.send_message(chat_id, "Usage: /session profile <name>", thread_id)
                return
            requested_profile = self._normalize_profile_name(args[1])
            if requested_profile not in self.config.execution_profiles:
                await self.telegram.send_message(
                    chat_id,
                    "Unknown profile. Allowed: " + ", ".join(sorted(self.config.execution_profiles)),
                    thread_id,
                )
                return
            try:
                self._authorize_command("session_profile", user_id, workspace_name=name, workspace_path=path, requested_profile_name=requested_profile)
                session = await self.sessions.apply_policy_change(name, path, profile_name=requested_profile, reason="session_profile_change")
            except PolicyAuthorizationError as exc:
                await self.telegram.send_message(chat_id, str(exc), thread_id)
                return
            except RuntimeError as exc:
                await self.telegram.send_message(chat_id, str(exc), thread_id)
                return
            await self.telegram.send_message(
                chat_id,
                (
                    f"Break-glass enabled until {session.break_glass_expires_at}. "
                    "A fresh session will be used for the next run."
                    if requested_profile == "break-glass"
                    else f"Profile set to {session.profile_name}. Sandbox={session.sandbox_mode}, approvals={session.approval_policy}, network={session.network_mode}. A fresh session will be used for the next run."
                ),
                thread_id,
            )
            return
        await self.telegram.send_message(chat_id, "Usage: /session [show|profile <name>|restart|reset]", thread_id)

    async def _debug_status(self, chat_id: int, thread_id: int | None) -> None:
        runtime = self.sessions.runtime_snapshot()
        lines = [f"Active runtimes: {len(runtime)}", f"Offset: {self._offset or 0}"]
        for item in runtime[:20]:
            lines.append(f"{item['workspace']} busy={item['busy']} idle={item['idle_seconds']}s path={item['path']}")
        await self.telegram.send_message(chat_id, "\n".join(lines), thread_id)

    async def _handle_prompt(
        self,
        scope: ChatScope,
        user_id: int,
        chat_id: int,
        thread_id: int | None,
        message_id: int | None,
        prompt: str,
        *,
        chat_type: str | None,
    ) -> None:
        resolved = self._workspace_from_scope(scope)
        if not resolved:
            await self.telegram.send_message(chat_id, "No workspace bound. Use /workspaces and /use <name>.", thread_id)
            return
        workspace_name, workspace_path = resolved
        response_ux_policy = self.config.response_ux.resolve_policy(chat_type=chat_type, thread_id=thread_id)
        context = TelegramResponseContext(
            identity=TelegramRequestIdentity(chat_id=chat_id, thread_id=thread_id, message_id=message_id),
            target=TelegramResponseTarget(chat_id=chat_id, thread_id=thread_id, reply_to_message_id=message_id),
            workspace_name=workspace_name,
            workspace_path=workspace_path,
            chat_type=chat_type,
            user_id=user_id,
            prompt=prompt,
            policy=response_ux_policy,
        )
        await self.response_ux.run(
            context,
            lambda on_event: self.sessions.execute(workspace_name, workspace_path, user_id, prompt, on_event),
        )

    def _authorize_command(
        self,
        command_name: str,
        user_id: int,
        *,
        workspace_name: str | None = None,
        workspace_path: str | None = None,
        requested_profile_name: str | None = None,
        requested_approval_policy: str | None = None,
        requested_command_rule_group: str | None = None,
    ) -> None:
        if self.policy_resolver is None:
            if self.config.trusted_admin_only_bind and command_name == "bind" and not self._is_admin(user_id):
                raise PolicyAuthorizationError("Admin only.")
            return
        self.policy_resolver.authorize_command(
            command_name=command_name,
            user_id=user_id,
            workspace_name=workspace_name,
            workspace_path=workspace_path,
            requested_profile_name=requested_profile_name,
            requested_approval_policy=requested_approval_policy,
            requested_command_rule_group=requested_command_rule_group,
        )

    def _normalize_profile_name(self, profile_name: str) -> str:
        aliases = {
            "breakglass": "break-glass",
            "break_glass": "break-glass",
            "bg": "break-glass",
        }
        return aliases.get(profile_name.lower(), profile_name.lower())
