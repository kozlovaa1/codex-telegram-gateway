from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .codex_adapter import CodexAdapter
from .codex_adapter import PolicyEnforcementError, ResolvedRunPolicy
from .execution_policy import ExecutionPolicyResolver
from .logging_utils import log_extra
from .models import CodexRunResult, utcnow_iso
from .workspace_preflight import WorkspacePreflightChecker, WorkspacePreflightError
from .workspace_store import WorkspaceStore


@dataclass(slots=True)
class WorkspaceRuntime:
    workspace_name: str
    workspace_path: str
    current_process: asyncio.subprocess.Process | None = None
    current_started_at: float | None = None
    current_queue_size: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used_monotonic: float = field(default_factory=time.monotonic)


class SessionManager:
    def __init__(
        self,
        store: WorkspaceStore,
        adapter: CodexAdapter,
        logger: logging.Logger,
        stream_edit_interval_seconds: float,
        session_idle_ttl_seconds: int,
        max_active_workspaces: int,
        max_parallel_processes: int,
        max_queue_per_workspace: int,
        policy_resolver: ExecutionPolicyResolver | None = None,
        preflight_checker: WorkspacePreflightChecker | None = None,
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.logger = logger
        self.policy_resolver = policy_resolver
        self.preflight_checker = preflight_checker
        self.stream_edit_interval_seconds = stream_edit_interval_seconds
        self.session_idle_ttl_seconds = session_idle_ttl_seconds
        self.max_active_workspaces = max_active_workspaces
        self.max_queue_per_workspace = max_queue_per_workspace
        self._runtimes: OrderedDict[str, WorkspaceRuntime] = OrderedDict()
        self._global_semaphore = asyncio.Semaphore(max_parallel_processes)

    def get_runtime(self, workspace_name: str, workspace_path: str) -> WorkspaceRuntime:
        runtime = self._runtimes.get(workspace_name)
        if runtime is None:
            self._evict_idle()
            runtime = WorkspaceRuntime(workspace_name=workspace_name, workspace_path=workspace_path)
            self._runtimes[workspace_name] = runtime
        runtime.last_used_monotonic = time.monotonic()
        self._runtimes.move_to_end(workspace_name)
        return runtime

    def _evict_idle(self) -> None:
        now = time.monotonic()
        idle_names = [
            name
            for name, runtime in self._runtimes.items()
            if runtime.current_process is None and now - runtime.last_used_monotonic > self.session_idle_ttl_seconds
        ]
        for name in idle_names:
            self._runtimes.pop(name, None)
        while len(self._runtimes) >= self.max_active_workspaces:
            oldest_name, oldest_runtime = next(iter(self._runtimes.items()))
            if oldest_runtime.current_process is not None:
                break
            self._runtimes.pop(oldest_name, None)

    async def stop_workspace(self, workspace_name: str) -> bool:
        runtime = self._runtimes.get(workspace_name)
        if not runtime or not runtime.current_process:
            return False
        await self.adapter._terminate(runtime.current_process)
        runtime.current_process = None
        self.store.update_session(
            workspace_name,
            busy_state="idle",
            busy_since=None,
            last_stop_reason="manual_stop",
        )
        log_extra(self.logger, "session_stop", workspace=workspace_name, reason="manual_stop")
        return True

    async def restart_workspace(self, workspace_name: str, workspace_path: str, reason: str = "manual_restart") -> None:
        runtime = self.get_runtime(workspace_name, workspace_path)
        async with runtime.lock:
            if self.preflight_checker is not None:
                preflight = self.preflight_checker.run(workspace_name, workspace_path)
                if not preflight.ok:
                    raise WorkspacePreflightError(preflight)
            if runtime.current_process is not None:
                await self.adapter._terminate(runtime.current_process)
                runtime.current_process = None
            self.store.update_session(
                workspace_name,
                session_id=None,
                busy_state="idle",
                busy_since=None,
                last_stop_reason=reason,
                last_restart_at=utcnow_iso(),
            )
            log_extra(self.logger, "session_restart", workspace=workspace_name, reason=reason)

    async def apply_policy_change(
        self,
        workspace_name: str,
        workspace_path: str,
        *,
        profile_name: str | None = None,
        sandbox_mode: str | None = None,
        approval_policy: str | None = None,
        network_mode: str | None = None,
        break_glass_expires_at: str | None | object = None,
        reason: str = "policy_change",
    ):
        runtime = self.get_runtime(workspace_name, workspace_path)
        if runtime.current_process is not None:
            log_extra(self.logger, "workspace_busy_conflict", workspace=workspace_name, reason=reason)
            raise RuntimeError(f"Workspace is busy: {workspace_name}")
        async with runtime.lock:
            if profile_name == "break-glass":
                expires_at = (
                    datetime.now(UTC) + timedelta(seconds=self.policy_resolver.config.break_glass_ttl_seconds)
                ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                session = self.store.update_session(
                    workspace_name,
                    break_glass_expires_at=expires_at,
                    session_id=None,
                    last_restart_at=utcnow_iso(),
                    last_stop_reason=reason,
                )
                log_extra(
                    self.logger,
                    "break_glass_enabled",
                    workspace=workspace_name,
                    reason=reason,
                    expires_at=expires_at,
                )
                log_extra(
                    self.logger,
                    "session_restart",
                    workspace=workspace_name,
                    reason=reason,
                    profile_name=profile_name,
                )
                return session
            if profile_name and self.policy_resolver is not None:
                profile = self.policy_resolver.config.execution_profiles[profile_name]
                sandbox_mode = sandbox_mode or profile.sandbox_mode
                approval_policy = approval_policy or profile.approval_policy
                network_mode = network_mode or profile.network_mode
            requires_restart = any(
                value is not None for value in (profile_name, sandbox_mode, approval_policy, network_mode)
            )
            update_kwargs = {
                "profile_name": profile_name,
                "sandbox_mode": sandbox_mode,
                "approval_policy": approval_policy,
                "network_mode": network_mode,
            }
            if break_glass_expires_at is not None:
                update_kwargs["break_glass_expires_at"] = break_glass_expires_at
            if requires_restart:
                update_kwargs["session_id"] = None
                update_kwargs["last_restart_at"] = utcnow_iso()
                update_kwargs["last_stop_reason"] = reason
            session = self.store.update_session(workspace_name, **update_kwargs)
            if requires_restart:
                log_extra(
                    self.logger,
                    "session_restart",
                    workspace=workspace_name,
                    reason=reason,
                    profile_name=session.profile_name,
                )
            return session

    async def execute(self, workspace_name: str, workspace_path: str, user_id: int, prompt: str, stream_callback) -> CodexRunResult:
        runtime = self.get_runtime(workspace_name, workspace_path)
        runtime.current_queue_size += 1
        if runtime.current_queue_size > self.max_queue_per_workspace:
            runtime.current_queue_size -= 1
            raise RuntimeError(f"Workspace queue limit reached for {workspace_name}")
        async with runtime.lock:
            try:
                runtime.last_used_monotonic = time.monotonic()
                if self.preflight_checker is not None:
                    preflight = self.preflight_checker.run(workspace_name, workspace_path)
                    if not preflight.ok:
                        raise WorkspacePreflightError(preflight)
                session = self.store.get_session(workspace_name)
                session = self._expire_break_glass_if_needed(workspace_name)
                run_policy = self._resolve_run_policy(workspace_name, workspace_path, user_id, session)
                self.store.update_session(
                    workspace_name,
                    busy_state="busy",
                    busy_since=utcnow_iso(),
                    last_stop_reason=None,
                )
                async with self._global_semaphore:
                    log_extra(
                        self.logger,
                        "session_start",
                        workspace=workspace_name,
                        path=workspace_path,
                        profile_name=run_policy.profile_name,
                        session_id=session.session_id or "",
                        sandbox_mode=run_policy.sandbox_mode,
                        approval_policy=run_policy.approval_policy,
                        network_mode=run_policy.network_mode,
                        command_rule_group=run_policy.command_rule_group,
                    )
                    result, _proc = await self.adapter.run(
                        workspace_path=workspace_path,
                        prompt=prompt,
                        session_id=session.session_id or None,
                        model=session.model,
                        policy=run_policy,
                        on_event=stream_callback,
                        on_process=self._process_started(runtime),
                    )
                    runtime.last_used_monotonic = time.monotonic()
                runtime.current_process = None
                self.store.update_session(
                    workspace_name,
                    session_id=(result.session_id or session.session_id),
                    busy_state="idle",
                    busy_since=None,
                    last_stop_reason="completed" if result.ok else "failed",
                    touch_last_used=True,
                )
                log_extra(
                    self.logger,
                    "codex.run.finish",
                    workspace=workspace_name,
                    exit_code=result.exit_code,
                    duration_seconds=round(result.duration_seconds, 3),
                    ok=result.ok,
                )
                return result
            except PolicyEnforcementError:
                self.store.update_session(
                    workspace_name,
                    busy_state="idle",
                    busy_since=None,
                    last_stop_reason="policy_rejected",
                )
                raise
            finally:
                runtime.current_process = None
                self.store.update_session(
                    workspace_name,
                    busy_state="idle",
                    busy_since=None,
                )
                runtime.current_queue_size -= 1

    def runtime_snapshot(self) -> list[dict[str, object]]:
        snapshot: list[dict[str, object]] = []
        now = time.monotonic()
        for name, runtime in self._runtimes.items():
            runtime_seconds = int(now - runtime.current_started_at) if runtime.current_started_at is not None and runtime.current_process is not None else 0
            snapshot.append(
                {
                    "workspace": name,
                    "path": runtime.workspace_path,
                    "busy": runtime.current_process is not None,
                    "queue_size": runtime.current_queue_size,
                    "idle_seconds": int(now - runtime.last_used_monotonic),
                    "runtime_seconds": runtime_seconds,
                }
            )
        return snapshot

    def _process_started(self, runtime: WorkspaceRuntime):
        def callback(proc: asyncio.subprocess.Process) -> None:
            runtime.current_process = proc
            runtime.current_started_at = time.monotonic()

        return callback

    def _expire_break_glass_if_needed(self, workspace_name: str):
        session = self.store.get_session(workspace_name)
        expires_at = session.break_glass_expires_at
        if not expires_at:
            return session
        try:
            expired = expires_at <= utcnow_iso()
        except TypeError:
            expired = False
        if not expired:
            return session
        self.store.update_execution_policy(workspace_name, break_glass_expires_at=None)
        log_extra(self.logger, "break_glass_expired", workspace=workspace_name, expired_at=expires_at)
        return self.store.get_session(workspace_name)

    def _resolve_run_policy(self, workspace_name: str, workspace_path: str, user_id: int, session) -> ResolvedRunPolicy:
        if self.policy_resolver is None:
            command_rule_group = "default"
            command_rules = ("workspace-safe",)
            return ResolvedRunPolicy(
                profile_name=session.profile_name,
                sandbox_mode=session.sandbox_mode,
                approval_policy=session.approval_policy,
                network_mode=session.network_mode,
                command_rule_group=command_rule_group,
                command_rules=command_rules,
            )
        resolved = self.policy_resolver.resolve(
            workspace_name=workspace_name,
            workspace_path=workspace_path,
            user_id=user_id,
            stored_policy=session.execution_policy,
        )
        return ResolvedRunPolicy(
            profile_name=resolved.profile_name,
            sandbox_mode=resolved.sandbox_mode,
            approval_policy=resolved.approval_policy,
            network_mode=resolved.network_mode,
            command_rule_group=resolved.command_rule_group,
            command_rules=resolved.command_rules,
        )
