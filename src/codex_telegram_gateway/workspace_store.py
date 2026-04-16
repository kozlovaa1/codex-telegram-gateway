from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import BindingRecord, ChatScope, SessionRecord, WorkspaceRecord, utcnow_iso


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS workspaces (
    name TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bindings (
    chat_id INTEGER NOT NULL,
    thread_key INTEGER NOT NULL,
    thread_id INTEGER,
    workspace_name TEXT NOT NULL REFERENCES workspaces(name) ON DELETE CASCADE,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(chat_id, thread_key)
);
CREATE TABLE IF NOT EXISTS sessions (
    workspace_name TEXT PRIMARY KEY REFERENCES workspaces(name) ON DELETE CASCADE,
    session_id TEXT,
    model TEXT,
    sandbox_mode TEXT NOT NULL,
    approval_policy TEXT NOT NULL,
    last_used_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class WorkspaceStore:
    def __init__(self, sqlite_path: Path, defaults: dict[str, str], default_model: str | None, default_sandbox_mode: str, default_approval_policy: str) -> None:
        self.sqlite_path = sqlite_path
        self.defaults = defaults
        self.default_model = default_model
        self.default_sandbox_mode = default_sandbox_mode
        self.default_approval_policy = default_approval_policy

    def initialize(self) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            for name, path in self.defaults.items():
                self.upsert_workspace(name, path, conn=conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def upsert_workspace(self, name: str, path: str, conn: sqlite3.Connection | None = None) -> None:
        owns_conn = conn is None
        conn = conn or self._connect()
        now = utcnow_iso()
        conn.execute(
            """
            INSERT INTO workspaces(name, path, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET path=excluded.path, updated_at=excluded.updated_at
            """,
            (name, path, now, now),
        )
        conn.commit()
        self.ensure_session(name, conn=conn)
        if owns_conn:
            conn.close()

    def list_workspaces(self) -> list[WorkspaceRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM workspaces ORDER BY name").fetchall()
        return [WorkspaceRecord(**dict(row)) for row in rows]

    def get_workspace(self, name: str) -> WorkspaceRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workspaces WHERE name = ?", (name,)).fetchone()
        return WorkspaceRecord(**dict(row)) if row else None

    def bind_scope(self, scope: ChatScope, workspace_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bindings(chat_id, thread_key, thread_id, workspace_name, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, thread_key) DO UPDATE SET workspace_name=excluded.workspace_name, thread_id=excluded.thread_id, updated_at=excluded.updated_at
                """,
                (scope.chat_id, scope.thread_id or 0, scope.thread_id, workspace_name, utcnow_iso()),
            )
            conn.commit()

    def get_binding(self, scope: ChatScope) -> BindingRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT chat_id, thread_id, workspace_name, updated_at FROM bindings WHERE chat_id = ? AND thread_key = ?",
                (scope.chat_id, scope.thread_id or 0),
            ).fetchone()
        return BindingRecord(**dict(row)) if row else None

    def ensure_session(self, workspace_name: str, conn: sqlite3.Connection | None = None) -> SessionRecord:
        owns_conn = conn is None
        conn = conn or self._connect()
        now = utcnow_iso()
        conn.execute(
            """
            INSERT INTO sessions(workspace_name, session_id, model, sandbox_mode, approval_policy, last_used_at, created_at, updated_at)
            VALUES(?, NULL, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(workspace_name) DO NOTHING
            """,
            (workspace_name, self.default_model, self.default_sandbox_mode, self.default_approval_policy, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sessions WHERE workspace_name = ?", (workspace_name,)).fetchone()
        if owns_conn:
            conn.close()
        assert row is not None
        return SessionRecord(**dict(row))

    def get_session(self, workspace_name: str) -> SessionRecord:
        with self._connect() as conn:
            return self.ensure_session(workspace_name, conn=conn)

    def update_session(self, workspace_name: str, *, session_id: str | None = None, model: str | None = None, sandbox_mode: str | None = None, approval_policy: str | None = None, touch_last_used: bool = False) -> SessionRecord:
        with self._connect() as conn:
            current = self.ensure_session(workspace_name, conn=conn)
            conn.execute(
                """
                UPDATE sessions
                SET session_id = ?, model = ?, sandbox_mode = ?, approval_policy = ?, last_used_at = ?, updated_at = ?
                WHERE workspace_name = ?
                """,
                (
                    session_id if session_id is not None else current.session_id,
                    model if model is not None else current.model,
                    sandbox_mode if sandbox_mode is not None else current.sandbox_mode,
                    approval_policy if approval_policy is not None else current.approval_policy,
                    utcnow_iso() if touch_last_used else current.last_used_at,
                    utcnow_iso(),
                    workspace_name,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM sessions WHERE workspace_name = ?", (workspace_name,)).fetchone()
        assert row is not None
        return SessionRecord(**dict(row))

    def reset_session(self, workspace_name: str) -> SessionRecord:
        return self.update_session(workspace_name, session_id="", touch_last_used=False)
