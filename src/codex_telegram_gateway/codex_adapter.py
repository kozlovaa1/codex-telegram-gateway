from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import time
from asyncio.subprocess import Process
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .models import CodexRunResult, RunEvent


LOGGER = logging.getLogger("codex_telegram_gateway.codex_adapter")
EventCallback = Callable[[RunEvent], Awaitable[None]]
ProcessCallback = Callable[[Process], None]


@dataclass(frozen=True, slots=True)
class AdapterCapabilityMatrix:
    sandbox_via_cli: bool = True
    model_via_cli: bool = True
    approval_via_cli: bool = False
    network_via_cli: bool = False
    command_rules_via_gateway: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedRunPolicy:
    profile_name: str
    sandbox_mode: str
    approval_policy: str
    network_mode: str
    command_rule_group: str
    command_rules: tuple[str, ...]


class PolicyEnforcementError(RuntimeError):
    pass


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


def normalize_run_event(event: dict[str, Any], *, session_id: str | None = None) -> RunEvent | None:
    raw_type = str(event.get("type", ""))
    text = extract_display_text(event)
    if raw_type == "thread.started" and isinstance(event.get("thread_id"), str):
        return RunEvent(
            kind="session_started",
            raw_type=raw_type,
            session_id=str(event["thread_id"]),
            payload=event,
        )
    if raw_type == "error":
        return RunEvent(
            kind="error",
            text=str(event.get("message", text)),
            raw_type=raw_type,
            session_id=session_id,
            payload=event,
        )
    if raw_type == "stderr":
        return RunEvent(
            kind="stderr",
            text=str(event.get("message", text)),
            raw_type=raw_type,
            session_id=session_id,
            payload=event,
        )
    if text:
        return RunEvent(
            kind="text_delta",
            text=text,
            raw_type=raw_type or None,
            session_id=session_id,
            payload=event,
        )
    if raw_type:
        return RunEvent(
            kind="lifecycle",
            raw_type=raw_type,
            session_id=session_id,
            payload=event,
        )
    return None


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
        self.capabilities = AdapterCapabilityMatrix()

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
        policy: ResolvedRunPolicy,
        on_event: EventCallback | None = None,
        on_process: ProcessCallback | None = None,
    ) -> tuple[CodexRunResult, Process]:
        self.prepare_runtime_home()
        output_last_message = self.runtime_dir / "output" / f"{uuid4()}.txt"
        self._validate_policy(policy)
        self._enforce_command_rules(prompt, workspace_path, policy)
        cmd = self.build_command(
            workspace_path=workspace_path,
            prompt=prompt,
            session_id=session_id,
            model=model,
            sandbox_mode=policy.sandbox_mode,
            output_last_message=output_last_message,
        )
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
                normalized_event = normalize_run_event(event, session_id=seen_session_id)
                if normalized_event and normalized_event.kind == "text_delta":
                    final_text_parts.append(normalized_event.text)
                if on_event:
                    if normalized_event is not None:
                        await on_event(normalized_event)

        async def read_stderr() -> None:
            assert proc.stderr is not None
            async for raw_line in proc.stderr:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line:
                    errors.append(line)
                    if on_event:
                        await on_event(
                            RunEvent(
                                kind="stderr",
                                text=line,
                                raw_type="stderr",
                                session_id=seen_session_id,
                                payload={"type": "stderr", "message": line},
                            )
                        )

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

    def build_command(
        self,
        *,
        workspace_path: str,
        prompt: str,
        session_id: str | None,
        model: str | None,
        sandbox_mode: str,
        output_last_message: Path,
    ) -> list[str]:
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
        return cmd

    def _validate_policy(self, policy: ResolvedRunPolicy) -> None:
        if policy.network_mode not in {"restricted", "enabled"}:
            raise PolicyEnforcementError(f"Unsupported network mode: {policy.network_mode}")
        if not self.capabilities.approval_via_cli and policy.approval_policy not in {"never", "untrusted", "on-request", "on-failure"}:
            raise PolicyEnforcementError(f"Unsupported approval policy: {policy.approval_policy}")
        if not self.capabilities.network_via_cli and policy.network_mode == "enabled":
            LOGGER.info(
                "adapter_policy_capability_fallback",
                extra={
                    "extra_fields": {
                        "control": "network_mode",
                        "mode": policy.network_mode,
                        "enforcement": "gateway-policy-only",
                    }
                },
            )

    def _enforce_command_rules(self, prompt: str, workspace_path: str, policy: ResolvedRunPolicy) -> None:
        lowered = prompt.lower()
        matched_rule: str | None = None
        if policy.command_rule_group == "default":
            for fragment in ("sudo ", "systemctl ", "journalctl ", "docker ", "kubectl ", "ssh ", "scp ", "rm -rf", "/etc/", "/var/"):
                if fragment in lowered:
                    matched_rule = fragment.strip()
                    break
        elif policy.command_rule_group == "ops":
            for fragment in ("sudo ", "mount ", "umount ", "iptables ", "ufw ", "shutdown", "reboot", "useradd ", "passwd "):
                if fragment in lowered:
                    matched_rule = fragment.strip()
                    break
        if matched_rule is None:
            return
        LOGGER.warning(
            "command_rule_violation",
            extra={
                "extra_fields": {
                    "workspace_path": workspace_path,
                    "command_rule_group": policy.command_rule_group,
                    "matched_rule": matched_rule,
                }
            },
        )
        raise PolicyEnforcementError(
            f"Prompt violates command rules for group {policy.command_rule_group}: {matched_rule}"
        )

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
