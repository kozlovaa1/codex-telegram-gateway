from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TelegramSettings:
    allow_private_chats: bool
    allow_group_chats: bool
    allow_topics: bool


@dataclass(slots=True)
class AppConfig:
    bot_name: str
    telegram_api_base: str
    telegram_token: str
    telegram_admin_ids: set[int]
    poll_timeout_seconds: int
    poll_retry_delay_seconds: int
    telegram_message_chunk: int
    stream_edit_interval_seconds: float
    status_port: int
    sqlite_path: Path
    runtime_dir: Path
    log_dir: Path
    codex_bin: str
    default_model: str | None
    default_sandbox_mode: str
    default_approval_policy: str
    session_idle_ttl_seconds: int
    command_timeout_seconds: int
    process_kill_grace_seconds: int
    max_parallel_processes: int
    max_queue_per_workspace: int
    max_active_workspaces: int
    per_user_rate_limit_window_seconds: int
    per_user_rate_limit_max_messages: int
    trusted_admin_only_bind: bool
    allowed_roots: list[Path]
    project_alias_roots: list[Path]
    workspace_defaults: dict[str, str]
    telegram: TelegramSettings


def _load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_config(config_path: str | os.PathLike[str], env_path: str | os.PathLike[str] | None = None) -> AppConfig:
    path = Path(config_path)
    if env_path:
        _load_dotenv(Path(env_path))
    else:
        _load_dotenv(path.parent / ".env")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    token = _require_env("TELEGRAM_BOT_TOKEN")
    admin_ids = {
        int(part.strip())
        for part in os.environ.get("TELEGRAM_ADMIN_IDS", "").split(",")
        if part.strip()
    }
    telegram_data = data.get("telegram", {})
    return AppConfig(
        bot_name=data.get("bot_name", "Codex Gateway"),
        telegram_api_base=data.get("telegram_api_base", "https://api.telegram.org"),
        telegram_token=token,
        telegram_admin_ids=admin_ids,
        poll_timeout_seconds=int(data.get("poll_timeout_seconds", 25)),
        poll_retry_delay_seconds=int(data.get("poll_retry_delay_seconds", 3)),
        telegram_message_chunk=int(data.get("telegram_message_chunk", 3900)),
        stream_edit_interval_seconds=float(data.get("stream_edit_interval_seconds", 2.0)),
        status_port=int(data.get("status_port", 8085)),
        sqlite_path=Path(data["sqlite_path"]),
        runtime_dir=Path(data["runtime_dir"]),
        log_dir=Path(data["log_dir"]),
        codex_bin=str(data["codex_bin"]),
        default_model=(str(data.get("default_model", "")).strip() or None),
        default_sandbox_mode=str(data.get("default_sandbox_mode", "workspace-write")),
        default_approval_policy=str(data.get("default_approval_policy", "never")),
        session_idle_ttl_seconds=int(data.get("session_idle_ttl_seconds", 21600)),
        command_timeout_seconds=int(data.get("command_timeout_seconds", 1800)),
        process_kill_grace_seconds=int(data.get("process_kill_grace_seconds", 8)),
        max_parallel_processes=int(data.get("max_parallel_processes", 2)),
        max_queue_per_workspace=int(data.get("max_queue_per_workspace", 8)),
        max_active_workspaces=int(data.get("max_active_workspaces", 16)),
        per_user_rate_limit_window_seconds=int(data.get("per_user_rate_limit_window_seconds", 20)),
        per_user_rate_limit_max_messages=int(data.get("per_user_rate_limit_max_messages", 6)),
        trusted_admin_only_bind=bool(data.get("trusted_admin_only_bind", True)),
        allowed_roots=[Path(p) for p in data.get("allowed_roots", [])],
        project_alias_roots=[Path(p) for p in data.get("project_alias_roots", ["/srv/projects"])],
        workspace_defaults={str(k): str(v) for k, v in data.get("workspace_defaults", {}).items()},
        telegram=TelegramSettings(
            allow_private_chats=bool(telegram_data.get("allow_private_chats", True)),
            allow_group_chats=bool(telegram_data.get("allow_group_chats", True)),
            allow_topics=bool(telegram_data.get("allow_topics", True)),
        ),
    )
