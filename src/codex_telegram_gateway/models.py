from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ChatScope:
    chat_id: int
    thread_id: int | None

    @property
    def key(self) -> str:
        return f"{self.chat_id}:{self.thread_id or 0}"


@dataclass(frozen=True, slots=True)
class TelegramResponseUxPolicy:
    scope_name: str
    allow_reaction: bool
    allow_typing: bool
    allow_progress_updates: bool
    allow_streaming_text: bool

    @property
    def final_only(self) -> bool:
        return not self.allow_progress_updates and not self.allow_streaming_text


@dataclass(frozen=True, slots=True)
class TelegramRequestIdentity:
    chat_id: int
    thread_id: int | None
    message_id: int | None

    @property
    def key(self) -> str:
        return f"{self.chat_id}:{self.thread_id or 0}:{self.message_id or 0}"


@dataclass(frozen=True, slots=True)
class TelegramResponseTarget:
    chat_id: int
    thread_id: int | None
    reply_to_message_id: int | None


@dataclass(frozen=True, slots=True)
class TelegramResponseContext:
    identity: TelegramRequestIdentity
    target: TelegramResponseTarget
    workspace_name: str
    workspace_path: str
    chat_type: str | None
    user_id: int
    prompt: str
    policy: TelegramResponseUxPolicy


@dataclass(slots=True)
class WorkspaceRecord:
    name: str
    path: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class BindingRecord:
    chat_id: int
    thread_id: int | None
    workspace_name: str
    updated_at: str


@dataclass(slots=True)
class ExecutionPolicyRecord:
    workspace_name: str
    profile_name: str
    override_scope: str
    sandbox_mode: str
    approval_policy: str
    network_mode: str
    command_rule_set_version: int
    break_glass_expires_at: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class SessionStateRecord:
    workspace_name: str
    session_id: str | None
    model: str | None
    busy_state: str
    busy_since: str | None
    last_stop_reason: str | None
    last_restart_at: str | None
    last_used_at: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class SessionRecord:
    workspace_name: str
    execution_policy: ExecutionPolicyRecord
    session_state: SessionStateRecord

    @property
    def profile_name(self) -> str:
        return self.execution_policy.profile_name

    @property
    def sandbox_mode(self) -> str:
        return self.execution_policy.sandbox_mode

    @property
    def approval_policy(self) -> str:
        return self.execution_policy.approval_policy

    @property
    def network_mode(self) -> str:
        return self.execution_policy.network_mode

    @property
    def command_rule_set_version(self) -> int:
        return self.execution_policy.command_rule_set_version

    @property
    def break_glass_expires_at(self) -> str | None:
        return self.execution_policy.break_glass_expires_at

    @property
    def session_id(self) -> str | None:
        return self.session_state.session_id

    @property
    def model(self) -> str | None:
        return self.session_state.model

    @property
    def busy_state(self) -> str:
        return self.session_state.busy_state

    @property
    def busy_since(self) -> str | None:
        return self.session_state.busy_since

    @property
    def last_stop_reason(self) -> str | None:
        return self.session_state.last_stop_reason

    @property
    def last_restart_at(self) -> str | None:
        return self.session_state.last_restart_at

    @property
    def last_used_at(self) -> str | None:
        return self.session_state.last_used_at

    @property
    def created_at(self) -> str:
        return self.session_state.created_at

    @property
    def updated_at(self) -> str:
        return self.session_state.updated_at

    @property
    def is_busy(self) -> bool:
        return self.busy_state != "idle"


@dataclass(slots=True)
class CodexRunResult:
    ok: bool
    final_text: str
    session_id: str | None
    exit_code: int
    duration_seconds: float
    errors: list[str]
    raw_events: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class RunEvent:
    kind: str
    text: str = ""
    raw_type: str | None = None
    session_id: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(slots=True)
class QueuedRequest:
    scope: ChatScope
    user_id: int
    prompt: str
    reply_chat_id: int
    reply_thread_id: int | None
    reply_to_message_id: int | None


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
