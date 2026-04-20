from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from .models import (
    BindingRecord,
    ChatScope,
    ExecutionPolicyRecord,
    SessionRecord,
    SessionStateRecord,
    WorkspaceRecord,
    utcnow_iso,
)


LOGGER = logging.getLogger("codex_telegram_gateway.workspace_store")
_UNSET = object()
VALID_BUSY_STATES = frozenset({"idle", "busy"})

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
CREATE TABLE IF NOT EXISTS execution_policies (
    workspace_name TEXT PRIMARY KEY REFERENCES workspaces(name) ON DELETE CASCADE,
    profile_name TEXT NOT NULL,
    override_scope TEXT NOT NULL,
    sandbox_mode TEXT NOT NULL,
    approval_policy TEXT NOT NULL,
    network_mode TEXT NOT NULL,
    command_rule_set_version INTEGER NOT NULL,
    break_glass_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_states (
    workspace_name TEXT PRIMARY KEY REFERENCES workspaces(name) ON DELETE CASCADE,
    session_id TEXT,
    model TEXT,
    busy_state TEXT NOT NULL,
    busy_since TEXT,
    last_stop_reason TEXT,
    last_restart_at TEXT,
    last_used_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class WorkspaceStore:
    def __init__(
        self,
        sqlite_path: Path,
        defaults: dict[str, str],
        default_model: str | None,
        default_sandbox_mode: str,
        default_approval_policy: str,
        default_network_mode: str = "restricted",
        default_profile_name: str = "default",
        default_command_rule_set_version: int = 1,
    ) -> None:
        self.sqlite_path = sqlite_path
        self.defaults = defaults
        self.default_model = default_model
        self.default_sandbox_mode = default_sandbox_mode
        self.default_approval_policy = default_approval_policy
        self.default_network_mode = default_network_mode
        self.default_profile_name = default_profile_name
        self.default_command_rule_set_version = default_command_rule_set_version

    def initialize(self) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            self._migrate_legacy_sessions(conn)
            for name, path in self.defaults.items():
                self.upsert_workspace(name, path, conn=conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _migrate_legacy_sessions(self, conn: sqlite3.Connection) -> None:
        self._ensure_execution_policy_columns(conn)
        if not self._table_exists(conn, "sessions"):
            return
        LOGGER.info("workspace_store_migration_started", extra={"extra_fields": {"operation": "legacy_sessions_to_split_tables"}})
        try:
            rows = conn.execute(
                """
                SELECT workspace_name, session_id, model, sandbox_mode, approval_policy, last_used_at, created_at, updated_at
                FROM sessions
                """
            ).fetchall()
            for row in rows:
                workspace_name = str(row["workspace_name"])
                created_at = str(row["created_at"])
                updated_at = str(row["updated_at"])
                conn.execute(
                    """
                    INSERT INTO execution_policies(
                        workspace_name, profile_name, override_scope, sandbox_mode, approval_policy, network_mode,
                        command_rule_set_version, break_glass_expires_at, created_at, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    ON CONFLICT(workspace_name) DO NOTHING
                    """,
                    (
                        workspace_name,
                        self.default_profile_name,
                        "durable-override",
                        row["sandbox_mode"],
                        row["approval_policy"],
                        self.default_network_mode,
                        self.default_command_rule_set_version,
                        created_at,
                        updated_at,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO session_states(
                        workspace_name, session_id, model, busy_state, busy_since,
                        last_stop_reason, last_restart_at, last_used_at, created_at, updated_at
                    )
                    VALUES(?, ?, ?, 'idle', NULL, NULL, NULL, ?, ?, ?)
                    ON CONFLICT(workspace_name) DO NOTHING
                    """,
                    (
                        workspace_name,
                        self._normalize_session_id(row["session_id"]),
                        row["model"],
                        row["last_used_at"],
                        created_at,
                        updated_at,
                    ),
                )
            conn.commit()
        except sqlite3.Error:
            LOGGER.error(
                "workspace_store_migration_failed",
                extra={"extra_fields": {"operation": "legacy_sessions_to_split_tables"}},
                exc_info=True,
            )
            raise
        LOGGER.info(
            "workspace_store_migration_finished",
            extra={
                "extra_fields": {
                    "operation": "legacy_sessions_to_split_tables",
                    "migrated_sessions": len(rows),
                }
            },
        )

    def _ensure_execution_policy_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(execution_policies)").fetchall()
        }
        if "override_scope" in columns:
            return
        LOGGER.info(
            "workspace_store_migration_started",
            extra={"extra_fields": {"operation": "execution_policies_add_override_scope"}},
        )
        try:
            conn.execute("ALTER TABLE execution_policies ADD COLUMN override_scope TEXT NOT NULL DEFAULT 'profile-default'")
            conn.execute(
                """
                UPDATE execution_policies
                SET override_scope = CASE
                    WHEN break_glass_expires_at IS NOT NULL THEN 'temporary-break-glass'
                    ELSE 'durable-override'
                END
                """
            )
            conn.commit()
        except sqlite3.Error:
            LOGGER.error(
                "workspace_store_migration_failed",
                extra={"extra_fields": {"operation": "execution_policies_add_override_scope"}},
                exc_info=True,
            )
            raise
        LOGGER.info(
            "workspace_store_migration_finished",
            extra={"extra_fields": {"operation": "execution_policies_add_override_scope"}},
        )

    def _row_to_policy(self, row: sqlite3.Row) -> ExecutionPolicyRecord:
        return ExecutionPolicyRecord(**dict(row))

    def _row_to_state(self, row: sqlite3.Row) -> SessionStateRecord:
        return SessionStateRecord(**dict(row))

    def _build_session_record(
        self,
        workspace_name: str,
        policy_row: sqlite3.Row,
        state_row: sqlite3.Row,
    ) -> SessionRecord:
        return SessionRecord(
            workspace_name=workspace_name,
            execution_policy=self._row_to_policy(policy_row),
            session_state=self._row_to_state(state_row),
        )

    def _log_policy_debug(self, event: str, workspace_name: str, **fields: object) -> None:
        LOGGER.debug(event, extra={"extra_fields": {"workspace_name": workspace_name, **fields}})

    def _normalize_session_id(self, session_id: str | None | object) -> str | None | object:
        if session_id is _UNSET:
            return _UNSET
        if session_id is None:
            return None
        if isinstance(session_id, str) and not session_id.strip():
            return None
        return session_id

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
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM workspaces ORDER BY name").fetchall()
        return [WorkspaceRecord(**dict(row)) for row in rows]

    def get_workspace(self, name: str) -> WorkspaceRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM workspaces WHERE name = ?", (name,)).fetchone()
        return WorkspaceRecord(**dict(row)) if row else None

    def bind_scope(self, scope: ChatScope, workspace_name: str) -> None:
        with closing(self._connect()) as conn:
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
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT chat_id, thread_id, workspace_name, updated_at FROM bindings WHERE chat_id = ? AND thread_key = ?",
                (scope.chat_id, scope.thread_id or 0),
            ).fetchone()
        return BindingRecord(**dict(row)) if row else None

    def ensure_execution_policy(self, workspace_name: str, conn: sqlite3.Connection | None = None) -> ExecutionPolicyRecord:
        owns_conn = conn is None
        conn = conn or self._connect()
        now = utcnow_iso()
        conn.execute(
            """
            INSERT INTO execution_policies(
                workspace_name, profile_name, override_scope, sandbox_mode, approval_policy, network_mode,
                command_rule_set_version, break_glass_expires_at, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(workspace_name) DO NOTHING
            """,
            (
                workspace_name,
                self.default_profile_name,
                "profile-default",
                self.default_sandbox_mode,
                self.default_approval_policy,
                self.default_network_mode,
                self.default_command_rule_set_version,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM execution_policies WHERE workspace_name = ?", (workspace_name,)).fetchone()
        row_data = dict(row) if row is not None else None
        if owns_conn:
            conn.close()
        assert row_data is not None
        self._log_policy_debug(
            "workspace_policy_read",
            workspace_name,
            profile_name=row_data["profile_name"],
            override_scope=row_data["override_scope"],
            sandbox_mode=row_data["sandbox_mode"],
            approval_policy=row_data["approval_policy"],
            network_mode=row_data["network_mode"],
        )
        return ExecutionPolicyRecord(**row_data)

    def ensure_session_state(self, workspace_name: str, conn: sqlite3.Connection | None = None) -> SessionStateRecord:
        owns_conn = conn is None
        conn = conn or self._connect()
        now = utcnow_iso()
        conn.execute(
            """
            INSERT INTO session_states(
                workspace_name, session_id, model, busy_state, busy_since,
                last_stop_reason, last_restart_at, last_used_at, created_at, updated_at
            )
            VALUES(?, NULL, ?, 'idle', NULL, NULL, NULL, NULL, ?, ?)
            ON CONFLICT(workspace_name) DO NOTHING
            """,
            (workspace_name, self.default_model, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM session_states WHERE workspace_name = ?", (workspace_name,)).fetchone()
        row_data = dict(row) if row is not None else None
        if owns_conn:
            conn.close()
        assert row_data is not None
        return SessionStateRecord(**row_data)

    def ensure_session(self, workspace_name: str, conn: sqlite3.Connection | None = None) -> SessionRecord:
        owns_conn = conn is None
        conn = conn or self._connect()
        self.ensure_execution_policy(workspace_name, conn=conn)
        self.ensure_session_state(workspace_name, conn=conn)
        policy_row = conn.execute("SELECT * FROM execution_policies WHERE workspace_name = ?", (workspace_name,)).fetchone()
        state_row = conn.execute("SELECT * FROM session_states WHERE workspace_name = ?", (workspace_name,)).fetchone()
        policy_row_data = dict(policy_row) if policy_row is not None else None
        state_row_data = dict(state_row) if state_row is not None else None
        if owns_conn:
            conn.close()
        assert policy_row_data is not None
        assert state_row_data is not None
        return SessionRecord(
            workspace_name=workspace_name,
            execution_policy=ExecutionPolicyRecord(**policy_row_data),
            session_state=SessionStateRecord(**state_row_data),
        )

    def get_session(self, workspace_name: str) -> SessionRecord:
        with closing(self._connect()) as conn:
            return self.ensure_session(workspace_name, conn=conn)

    def update_execution_policy(
        self,
        workspace_name: str,
        *,
        profile_name: str | None = None,
        override_scope: str | None = None,
        sandbox_mode: str | None = None,
        approval_policy: str | None = None,
        network_mode: str | None = None,
        command_rule_set_version: int | None = None,
        break_glass_expires_at: str | None | object = _UNSET,
    ) -> ExecutionPolicyRecord:
        with closing(self._connect()) as conn:
            current = self.ensure_execution_policy(workspace_name, conn=conn)
            next_break_glass_expires_at = (
                current.break_glass_expires_at if break_glass_expires_at is _UNSET else break_glass_expires_at
            )
            next_override_scope = override_scope
            if next_override_scope is None and any(
                value is not None
                for value in (profile_name, sandbox_mode, approval_policy, network_mode, command_rule_set_version)
            ):
                next_override_scope = "durable-override"
            changed_fields: dict[str, object] = {}
            next_values = {
                "profile_name": profile_name if profile_name is not None else current.profile_name,
                "override_scope": next_override_scope if next_override_scope is not None else current.override_scope,
                "sandbox_mode": sandbox_mode if sandbox_mode is not None else current.sandbox_mode,
                "approval_policy": approval_policy if approval_policy is not None else current.approval_policy,
                "network_mode": network_mode if network_mode is not None else current.network_mode,
                "command_rule_set_version": (
                    command_rule_set_version
                    if command_rule_set_version is not None
                    else current.command_rule_set_version
                ),
                "break_glass_expires_at": next_break_glass_expires_at,
            }
            for key, value in next_values.items():
                if getattr(current, key) != value:
                    changed_fields[key] = value
            if not changed_fields:
                return current
            now = utcnow_iso()
            conn.execute(
                """
                UPDATE execution_policies
                SET profile_name = ?, override_scope = ?, sandbox_mode = ?, approval_policy = ?, network_mode = ?,
                    command_rule_set_version = ?, break_glass_expires_at = ?, updated_at = ?
                WHERE workspace_name = ?
                """,
                (
                    next_values["profile_name"],
                    next_values["override_scope"],
                    next_values["sandbox_mode"],
                    next_values["approval_policy"],
                    next_values["network_mode"],
                    next_values["command_rule_set_version"],
                    next_values["break_glass_expires_at"],
                    now,
                    workspace_name,
                ),
            )
            conn.commit()
            self._log_policy_debug("workspace_policy_updated", workspace_name, **changed_fields)
            row = conn.execute("SELECT * FROM execution_policies WHERE workspace_name = ?", (workspace_name,)).fetchone()
            row_data = dict(row) if row is not None else None
        assert row_data is not None
        return ExecutionPolicyRecord(**row_data)

    def update_session(
        self,
        workspace_name: str,
        *,
        session_id: str | None | object = _UNSET,
        model: str | None | object = _UNSET,
        sandbox_mode: str | None = None,
        approval_policy: str | None = None,
        network_mode: str | None = None,
        profile_name: str | None = None,
        override_scope: str | None = None,
        command_rule_set_version: int | None = None,
        break_glass_expires_at: str | None | object = _UNSET,
        busy_state: str | None = None,
        busy_since: str | None | object = _UNSET,
        last_stop_reason: str | None | object = _UNSET,
        last_restart_at: str | None | object = _UNSET,
        touch_last_used: bool = False,
    ) -> SessionRecord:
        with closing(self._connect()) as conn:
            current = self.ensure_session(workspace_name, conn=conn)
            next_override_scope = override_scope
            if next_override_scope is None and any(
                value is not None
                for value in (profile_name, sandbox_mode, approval_policy, network_mode, command_rule_set_version)
            ):
                next_override_scope = "durable-override"
            self._update_execution_policy_in_conn(
                conn,
                workspace_name,
                current.execution_policy,
                profile_name=profile_name,
                override_scope=next_override_scope,
                sandbox_mode=sandbox_mode,
                approval_policy=approval_policy,
                network_mode=network_mode,
                command_rule_set_version=command_rule_set_version,
                break_glass_expires_at=break_glass_expires_at,
            )

            if busy_state is not None and busy_state not in VALID_BUSY_STATES:
                raise ValueError(f"Invalid busy state: {busy_state}")
            normalized_session_id = self._normalize_session_id(session_id)
            next_session_id = current.session_id if normalized_session_id is _UNSET else normalized_session_id
            next_model = current.model if model is _UNSET else model
            next_busy_since = current.busy_since if busy_since is _UNSET else busy_since
            next_last_stop_reason = current.last_stop_reason if last_stop_reason is _UNSET else last_stop_reason
            next_last_restart_at = current.last_restart_at if last_restart_at is _UNSET else last_restart_at
            next_last_used_at = utcnow_iso() if touch_last_used else current.last_used_at
            next_busy_state = busy_state if busy_state is not None else current.busy_state
            changed_fields: dict[str, object] = {}
            next_values = {
                "session_id": next_session_id,
                "model": next_model,
                "busy_state": next_busy_state,
                "busy_since": next_busy_since,
                "last_stop_reason": next_last_stop_reason,
                "last_restart_at": next_last_restart_at,
                "last_used_at": next_last_used_at,
            }
            for key, value in next_values.items():
                if getattr(current.session_state, key) != value:
                    changed_fields[key] = value
            if changed_fields:
                now = utcnow_iso()
                conn.execute(
                    """
                    UPDATE session_states
                    SET session_id = ?, model = ?, busy_state = ?, busy_since = ?,
                        last_stop_reason = ?, last_restart_at = ?, last_used_at = ?, updated_at = ?
                    WHERE workspace_name = ?
                    """,
                    (
                        next_values["session_id"],
                        next_values["model"],
                        next_values["busy_state"],
                        next_values["busy_since"],
                        next_values["last_stop_reason"],
                        next_values["last_restart_at"],
                        next_values["last_used_at"],
                        now,
                        workspace_name,
                    ),
                )
                conn.commit()
            policy_row = conn.execute("SELECT * FROM execution_policies WHERE workspace_name = ?", (workspace_name,)).fetchone()
            state_row = conn.execute("SELECT * FROM session_states WHERE workspace_name = ?", (workspace_name,)).fetchone()
            policy_row_data = dict(policy_row) if policy_row is not None else None
            state_row_data = dict(state_row) if state_row is not None else None
        assert policy_row_data is not None
        assert state_row_data is not None
        return SessionRecord(
            workspace_name=workspace_name,
            execution_policy=ExecutionPolicyRecord(**policy_row_data),
            session_state=SessionStateRecord(**state_row_data),
        )

    def _update_execution_policy_in_conn(
        self,
        conn: sqlite3.Connection,
        workspace_name: str,
        current: ExecutionPolicyRecord,
        *,
        profile_name: str | None,
        override_scope: str | None,
        sandbox_mode: str | None,
        approval_policy: str | None,
        network_mode: str | None,
        command_rule_set_version: int | None,
        break_glass_expires_at: str | None | object,
    ) -> None:
        next_break_glass_expires_at = current.break_glass_expires_at if break_glass_expires_at is _UNSET else break_glass_expires_at
        next_override_scope = override_scope
        if next_override_scope is None and any(
            value is not None
            for value in (profile_name, sandbox_mode, approval_policy, network_mode, command_rule_set_version)
        ):
            next_override_scope = "durable-override"
        changed_fields: dict[str, object] = {}
        next_values = {
            "profile_name": profile_name if profile_name is not None else current.profile_name,
            "override_scope": next_override_scope if next_override_scope is not None else current.override_scope,
            "sandbox_mode": sandbox_mode if sandbox_mode is not None else current.sandbox_mode,
            "approval_policy": approval_policy if approval_policy is not None else current.approval_policy,
            "network_mode": network_mode if network_mode is not None else current.network_mode,
            "command_rule_set_version": (
                command_rule_set_version if command_rule_set_version is not None else current.command_rule_set_version
            ),
            "break_glass_expires_at": next_break_glass_expires_at,
        }
        for key, value in next_values.items():
            if getattr(current, key) != value:
                changed_fields[key] = value
        if not changed_fields:
            return
        now = utcnow_iso()
        conn.execute(
            """
            UPDATE execution_policies
            SET profile_name = ?, override_scope = ?, sandbox_mode = ?, approval_policy = ?, network_mode = ?,
                command_rule_set_version = ?, break_glass_expires_at = ?, updated_at = ?
            WHERE workspace_name = ?
            """,
            (
                next_values["profile_name"],
                next_values["override_scope"],
                next_values["sandbox_mode"],
                next_values["approval_policy"],
                next_values["network_mode"],
                next_values["command_rule_set_version"],
                next_values["break_glass_expires_at"],
                now,
                workspace_name,
            ),
        )
        conn.commit()
        self._log_policy_debug("workspace_policy_updated", workspace_name, **changed_fields)

    def reset_session(self, workspace_name: str) -> SessionRecord:
        return self.update_session(workspace_name, session_id=None, touch_last_used=False)
