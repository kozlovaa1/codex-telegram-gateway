from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from .codex_adapter import CodexAdapter
from .logging_utils import log_extra
from .models import CodexRunResult
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
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.logger = logger
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
        return True

    async def execute(self, workspace_name: str, workspace_path: str, prompt: str, stream_callback) -> CodexRunResult:
        runtime = self.get_runtime(workspace_name, workspace_path)
        runtime.current_queue_size += 1
        if runtime.current_queue_size > self.max_queue_per_workspace:
            runtime.current_queue_size -= 1
            raise RuntimeError(f"Workspace queue limit reached for {workspace_name}")
        async with runtime.lock:
            try:
                runtime.last_used_monotonic = time.monotonic()
                session = self.store.get_session(workspace_name)
                async with self._global_semaphore:
                    log_extra(self.logger, "codex.run.start", workspace=workspace_name, path=workspace_path, session_id=session.session_id or "")
                    result, _proc = await self.adapter.run(
                        workspace_path=workspace_path,
                        prompt=prompt,
                        session_id=session.session_id or None,
                        model=session.model,
                        sandbox_mode=session.sandbox_mode,
                        approval_policy=session.approval_policy,
                        on_event=stream_callback,
                        on_process=self._process_started(runtime),
                    )
                    runtime.last_used_monotonic = time.monotonic()
                runtime.current_process = None
                self.store.update_session(
                    workspace_name,
                    session_id=(result.session_id or session.session_id or ""),
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
            finally:
                runtime.current_process = None
                runtime.current_queue_size -= 1

    def runtime_snapshot(self) -> list[dict[str, object]]:
        snapshot: list[dict[str, object]] = []
        now = time.monotonic()
        for name, runtime in self._runtimes.items():
            snapshot.append(
                {
                    "workspace": name,
                    "path": runtime.workspace_path,
                    "busy": runtime.current_process is not None,
                    "queue_size": runtime.current_queue_size,
                    "idle_seconds": int(now - runtime.last_used_monotonic),
                }
            )
        return snapshot

    def _process_started(self, runtime: WorkspaceRuntime):
        def callback(proc: asyncio.subprocess.Process) -> None:
            runtime.current_process = proc
            runtime.current_started_at = time.monotonic()

        return callback
