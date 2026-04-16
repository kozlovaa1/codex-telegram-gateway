from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import time
from asyncio.subprocess import Process
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .models import CodexRunResult


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
ProcessCallback = Callable[[Process], None]


def extract_display_text(event: dict[str, Any]) -> str:
    candidates: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"delta", "text", "message"} and isinstance(item, str):
                    candidates.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(event)
    event_type = str(event.get("type", ""))
    if event_type in {"error"} and isinstance(event.get("message"), str):
        candidates.append(str(event["message"]))
    return "".join(part for part in candidates if part).strip()


class CodexAdapter:
    def __init__(
        self,
        codex_bin: str,
        runtime_dir: Path,
        timeout_seconds: int,
        kill_grace_seconds: int,
        auth_source_home: Path | None = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.runtime_dir = runtime_dir
        self.timeout_seconds = timeout_seconds
        self.kill_grace_seconds = kill_grace_seconds
        self.auth_source_home = auth_source_home

    def runtime_home(self) -> Path:
        return self.runtime_dir / "home"

    def env(self) -> dict[str, str]:
        home = self.runtime_home()
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        env["XDG_DATA_HOME"] = str(home / ".local" / "share")
        env["XDG_STATE_HOME"] = str(home / ".local" / "state")
        env["XDG_CACHE_HOME"] = str(home / ".cache")
        return env

    def prepare_runtime_home(self) -> None:
        home = self.runtime_home()
        home.mkdir(parents=True, exist_ok=True)
        (home / ".config").mkdir(parents=True, exist_ok=True)
        (home / ".local" / "share").mkdir(parents=True, exist_ok=True)
        (home / ".local" / "state").mkdir(parents=True, exist_ok=True)
        (home / ".cache").mkdir(parents=True, exist_ok=True)
        (self.runtime_dir / "output").mkdir(parents=True, exist_ok=True)
        self._sync_auth_from_source(home)

    def _sync_auth_from_source(self, runtime_home: Path) -> None:
        if os.environ.get("OPENAI_API_KEY"):
            return
        source_home = self.auth_source_home or Path.home() / ".codex"
        source_auth = source_home / "auth.json"
        if not source_auth.exists() or not source_auth.is_file():
            return
        target_auth = runtime_home / ".codex" / "auth.json"
        target_auth.parent.mkdir(parents=True, exist_ok=True)
        should_copy = True
        if target_auth.exists():
            try:
                should_copy = source_auth.stat().st_mtime_ns > target_auth.stat().st_mtime_ns
            except FileNotFoundError:
                should_copy = True
        if should_copy:
            shutil.copy2(source_auth, target_auth)

    async def run(
        self,
        *,
        workspace_path: str,
        prompt: str,
        session_id: str | None,
        model: str | None,
        sandbox_mode: str,
        approval_policy: str,
        on_event: EventCallback | None = None,
        on_process: ProcessCallback | None = None,
    ) -> tuple[CodexRunResult, Process]:
        self.prepare_runtime_home()
        output_last_message = self.runtime_dir / "output" / f"{uuid4()}.txt"
        cmd = [
            self.codex_bin,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--cd",
            workspace_path,
            "--sandbox",
            sandbox_mode,
            "--output-last-message",
            str(output_last_message),
        ]
        if model:
            cmd.extend(["--model", model])
        if session_id:
            cmd.extend(["resume", session_id, prompt])
        else:
            cmd.append(prompt)
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env(),
        )
        if on_process:
            on_process(proc)
        raw_events: list[dict[str, Any]] = []
        errors: list[str] = []
        final_text_parts: list[str] = []
        seen_session_id = session_id

        async def read_stdout() -> None:
            nonlocal seen_session_id
            assert proc.stdout is not None
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = {"type": "stdout", "text": line}
                raw_events.append(event)
                if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
                    seen_session_id = event["thread_id"]
                text = extract_display_text(event)
                if text and event.get("type") not in {"error", "turn.started"}:
                    final_text_parts.append(text)
                if on_event:
                    await on_event(event)

        async def read_stderr() -> None:
            assert proc.stderr is not None
            async for raw_line in proc.stderr:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line:
                    errors.append(line)
                    if on_event:
                        await on_event({"type": "stderr", "message": line})

        readers = [asyncio.create_task(read_stdout()), asyncio.create_task(read_stderr())]
        try:
            await asyncio.wait_for(proc.wait(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            await self._terminate(proc)
            errors.append(f"Timed out after {self.timeout_seconds} seconds")
        finally:
            await asyncio.gather(*readers, return_exceptions=True)
        duration = time.monotonic() - started
        final_text = "\n".join(part for part in final_text_parts if part).strip()
        if output_last_message.exists():
            try:
                output_text = output_last_message.read_text(encoding="utf-8").strip()
                if output_text:
                    final_text = output_text
            finally:
                output_last_message.unlink(missing_ok=True)
        result = CodexRunResult(
            ok=(proc.returncode == 0),
            final_text=final_text,
            session_id=seen_session_id,
            exit_code=proc.returncode or 0,
            duration_seconds=duration,
            errors=errors,
            raw_events=raw_events,
        )
        return result, proc

    async def _terminate(self, proc: Process) -> None:
        if proc.returncode is not None:
            return
        proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=self.kill_grace_seconds)
            return
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
