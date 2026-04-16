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
class SessionRecord:
    workspace_name: str
    session_id: str | None
    model: str | None
    sandbox_mode: str
    approval_policy: str
    last_used_at: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class CodexRunResult:
    ok: bool
    final_text: str
    session_id: str | None
    exit_code: int
    duration_seconds: float
    errors: list[str]
    raw_events: list[dict[str, Any]]


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
