from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import TelegramResponseUxPolicy


LOGGER = logging.getLogger("codex_telegram_gateway.config")

VALID_SANDBOX_MODES = frozenset({"read-only", "workspace-write", "danger-full-access"})
VALID_APPROVAL_POLICIES = frozenset({"never", "on-request", "on-failure", "untrusted"})
VALID_NETWORK_MODES = frozenset({"restricted", "enabled"})
PROFILE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    name: str
    sandbox_mode: str
    approval_policy: str
    network_mode: str
    command_rule_group: str
    admin_only: bool


@dataclass(frozen=True, slots=True)
class AdminOnlySettings:
    bind: bool
    use: bool
    execmode: bool
    approvals: bool
    break_glass: bool
    command_rule_overrides: bool


@dataclass(slots=True)
class TelegramSettings:
    allow_private_chats: bool
    allow_group_chats: bool
    allow_topics: bool


@dataclass(frozen=True, slots=True)
class ResponseUxScopeSettings:
    reaction: bool
    typing: bool
    progress: bool
    stream: bool

    def validate(self, *, field_prefix: str) -> None:
        if self.stream and not self.progress:
            raise _config_error(
                "response_ux_invalid",
                field=field_prefix,
                detail="stream requires progress to be enabled",
            )

    def to_policy(self, *, scope_name: str) -> TelegramResponseUxPolicy:
        return TelegramResponseUxPolicy(
            scope_name=scope_name,
            allow_reaction=self.reaction,
            allow_typing=self.typing,
            allow_progress_updates=self.progress,
            allow_streaming_text=self.stream,
        )


@dataclass(frozen=True, slots=True)
class ResponseUxSettings:
    private_chat: ResponseUxScopeSettings
    group_chat: ResponseUxScopeSettings

    def resolve_policy(self, *, chat_type: str | None, thread_id: int | None) -> TelegramResponseUxPolicy:
        if chat_type == "private":
            return self.private_chat.to_policy(scope_name="private")
        scope_name = "group-topic" if thread_id is not None else "group"
        return self.group_chat.to_policy(scope_name=scope_name)


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
    codex_auth_source_home: Path | None
    default_workspace_name: str | None
    default_model: str | None
    default_sandbox_mode: str
    default_approval_policy: str
    default_network_mode: str
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
    workspace_profile_defaults: dict[str, str]
    execution_profiles: dict[str, ExecutionProfile]
    command_rule_groups: dict[str, tuple[str, ...]]
    admin_only: AdminOnlySettings
    break_glass_ttl_seconds: int
    telegram: TelegramSettings
    response_ux: ResponseUxSettings


class ConfigError(RuntimeError):
    """Raised when the gateway configuration is invalid."""


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


def _log_validation_error(reason: str, **fields: object) -> None:
    LOGGER.error(reason, extra={"extra_fields": fields})


def _config_error(reason: str, **fields: object) -> ConfigError:
    _log_validation_error(reason, **fields)
    details = ", ".join(f"{key}={value!r}" for key, value in fields.items())
    if details:
        return ConfigError(f"{reason}: {details}")
    return ConfigError(reason)


def _require_table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if isinstance(value, dict):
        return value
    raise _config_error("config_table_invalid", table=key, value_type=type(value).__name__)


def _coerce_str(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _config_error("config_value_invalid", field=field_name, value_type=type(value).__name__)
    stripped = value.strip()
    if not stripped and not allow_empty:
        raise _config_error("config_value_empty", field=field_name)
    return stripped


def _coerce_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise _config_error("config_value_invalid", field=field_name, value_type=type(value).__name__)
    return value


def _coerce_int(value: Any, *, field_name: str, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _config_error("config_value_invalid", field=field_name, value_type=type(value).__name__)
    if minimum is not None and value < minimum:
        raise _config_error("config_value_out_of_range", field=field_name, minimum=minimum, actual=value)
    if maximum is not None and value > maximum:
        raise _config_error("config_value_out_of_range", field=field_name, maximum=maximum, actual=value)
    return value


def _validate_profile_name(name: str, *, field_name: str) -> str:
    if not PROFILE_NAME_PATTERN.fullmatch(name):
        raise _config_error("profile_name_invalid", field=field_name, profile_name=name)
    return name


def _validate_choice(value: str, *, field_name: str, allowed: frozenset[str]) -> str:
    if value not in allowed:
        raise _config_error("config_value_invalid", field=field_name, actual=value, allowed=sorted(allowed))
    return value


def _validate_rule_group_name(name: str, *, field_name: str) -> str:
    if not PROFILE_NAME_PATTERN.fullmatch(name):
        raise _config_error("rule_group_invalid", field=field_name, rule_group=name)
    return name


def _parse_command_rule_groups(data: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    groups: dict[str, tuple[str, ...]] = {
        "default": ("workspace-safe",),
        "ops": ("workspace-safe", "ops-read", "ops-write"),
        "break-glass": ("workspace-safe", "ops-read", "ops-write", "break-glass"),
    }
    raw_groups = _require_table(data, "command_rule_groups")
    for group_name, raw_rules in raw_groups.items():
        validated_name = _validate_rule_group_name(str(group_name).strip(), field_name=f"command_rule_groups.{group_name}")
        if not isinstance(raw_rules, list):
            raise _config_error("rule_group_rules_invalid", rule_group=validated_name, value_type=type(raw_rules).__name__)
        parsed_rules: list[str] = []
        for index, rule in enumerate(raw_rules):
            if not isinstance(rule, str) or not rule.strip():
                raise _config_error(
                    "rule_group_rule_invalid",
                    rule_group=validated_name,
                    rule_index=index,
                    value_type=type(rule).__name__,
                )
            parsed_rules.append(rule.strip())
        groups[validated_name] = tuple(parsed_rules)
    return groups


def _base_execution_profiles(data: dict[str, Any]) -> dict[str, ExecutionProfile]:
    default_sandbox_mode = _validate_choice(
        str(data.get("default_sandbox_mode", "workspace-write")).strip(),
        field_name="default_sandbox_mode",
        allowed=VALID_SANDBOX_MODES,
    )
    default_approval_policy = _validate_choice(
        str(data.get("default_approval_policy", "never")).strip(),
        field_name="default_approval_policy",
        allowed=VALID_APPROVAL_POLICIES,
    )
    default_network_mode = _validate_choice(
        str(data.get("default_network_mode", "restricted")).strip(),
        field_name="default_network_mode",
        allowed=VALID_NETWORK_MODES,
    )
    return {
        "default": ExecutionProfile(
            name="default",
            sandbox_mode=default_sandbox_mode,
            approval_policy=default_approval_policy,
            network_mode=default_network_mode,
            command_rule_group="default",
            admin_only=False,
        ),
        "ops": ExecutionProfile(
            name="ops",
            sandbox_mode="workspace-write",
            approval_policy="on-request",
            network_mode="restricted",
            command_rule_group="ops",
            admin_only=True,
        ),
        "break-glass": ExecutionProfile(
            name="break-glass",
            sandbox_mode="danger-full-access",
            approval_policy="never",
            network_mode="enabled",
            command_rule_group="break-glass",
            admin_only=True,
        ),
    }


def _parse_execution_profiles(
    data: dict[str, Any],
    *,
    command_rule_groups: dict[str, tuple[str, ...]],
) -> dict[str, ExecutionProfile]:
    profiles = dict(_base_execution_profiles(data))
    raw_profiles = _require_table(data, "execution_profiles")
    for profile_name, raw_profile in raw_profiles.items():
        validated_name = _validate_profile_name(str(profile_name).strip(), field_name=f"execution_profiles.{profile_name}")
        if not isinstance(raw_profile, dict):
            raise _config_error("profile_config_invalid", profile_name=validated_name, value_type=type(raw_profile).__name__)
        base_profile = profiles.get(validated_name)
        sandbox_mode = _validate_choice(
            _coerce_str(raw_profile.get("sandbox_mode", base_profile.sandbox_mode if base_profile else "workspace-write"), field_name=f"execution_profiles.{validated_name}.sandbox_mode"),
            field_name=f"execution_profiles.{validated_name}.sandbox_mode",
            allowed=VALID_SANDBOX_MODES,
        )
        approval_policy = _validate_choice(
            _coerce_str(raw_profile.get("approval_policy", base_profile.approval_policy if base_profile else "never"), field_name=f"execution_profiles.{validated_name}.approval_policy"),
            field_name=f"execution_profiles.{validated_name}.approval_policy",
            allowed=VALID_APPROVAL_POLICIES,
        )
        network_mode = _validate_choice(
            _coerce_str(raw_profile.get("network_mode", base_profile.network_mode if base_profile else "restricted"), field_name=f"execution_profiles.{validated_name}.network_mode"),
            field_name=f"execution_profiles.{validated_name}.network_mode",
            allowed=VALID_NETWORK_MODES,
        )
        command_rule_group = _coerce_str(
            raw_profile.get("command_rule_group", base_profile.command_rule_group if base_profile else "default"),
            field_name=f"execution_profiles.{validated_name}.command_rule_group",
        )
        if command_rule_group not in command_rule_groups:
            raise _config_error(
                "rule_group_reference_invalid",
                profile_name=validated_name,
                rule_group=command_rule_group,
            )
        admin_only = _coerce_bool(
            raw_profile.get("admin_only", base_profile.admin_only if base_profile else False),
            field_name=f"execution_profiles.{validated_name}.admin_only",
        )
        profiles[validated_name] = ExecutionProfile(
            name=validated_name,
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
            network_mode=network_mode,
            command_rule_group=command_rule_group,
            admin_only=admin_only,
        )

    default_profile = profiles["default"]
    if default_profile.admin_only:
        raise _config_error("unsafe_default_profile", profile_name="default", detail="default profile cannot be admin_only")
    if default_profile.sandbox_mode == "danger-full-access":
        raise _config_error("unsafe_default_profile", profile_name="default", detail="default profile cannot use danger-full-access")
    if default_profile.network_mode == "enabled":
        raise _config_error("unsafe_default_profile", profile_name="default", detail="default profile cannot enable network by default")
    if not profiles["ops"].admin_only:
        raise _config_error("unsafe_profile_override", profile_name="ops", detail="ops profile must remain admin_only")
    if not profiles["break-glass"].admin_only:
        raise _config_error("unsafe_profile_override", profile_name="break-glass", detail="break-glass profile must remain admin_only")
    return profiles


def _parse_workspace_profile_defaults(
    data: dict[str, Any],
    *,
    profiles: dict[str, ExecutionProfile],
) -> dict[str, str]:
    raw_defaults = _require_table(data, "workspace_profile_defaults")
    workspace_profile_defaults: dict[str, str] = {}
    for raw_selector, raw_profile_name in raw_defaults.items():
        selector = str(raw_selector).strip()
        if not selector:
            raise _config_error("workspace_selector_invalid", workspace_selector=raw_selector)
        profile_name = _coerce_str(raw_profile_name, field_name=f"workspace_profile_defaults.{selector}")
        if profile_name not in profiles:
            raise _config_error(
                "workspace_profile_reference_invalid",
                workspace_selector=selector,
                profile_name=profile_name,
            )
        workspace_profile_defaults[selector] = profile_name
    return workspace_profile_defaults


def _parse_admin_only_settings(data: dict[str, Any]) -> AdminOnlySettings:
    raw_settings = _require_table(data, "admin_only")
    return AdminOnlySettings(
        bind=_coerce_bool(raw_settings.get("bind", bool(data.get("trusted_admin_only_bind", True))), field_name="admin_only.bind"),
        use=_coerce_bool(raw_settings.get("use", False), field_name="admin_only.use"),
        execmode=_coerce_bool(raw_settings.get("execmode", True), field_name="admin_only.execmode"),
        approvals=_coerce_bool(raw_settings.get("approvals", True), field_name="admin_only.approvals"),
        break_glass=_coerce_bool(raw_settings.get("break_glass", True), field_name="admin_only.break_glass"),
        command_rule_overrides=_coerce_bool(
            raw_settings.get("command_rule_overrides", True),
            field_name="admin_only.command_rule_overrides",
        ),
    )


def default_response_ux_settings() -> ResponseUxSettings:
    return ResponseUxSettings(
        private_chat=ResponseUxScopeSettings(
            reaction=True,
            typing=True,
            progress=True,
            stream=True,
        ),
        group_chat=ResponseUxScopeSettings(
            reaction=True,
            typing=True,
            progress=False,
            stream=False,
        ),
    )


def _parse_response_ux_scope(
    raw_scope: Any,
    *,
    field_prefix: str,
    defaults: ResponseUxScopeSettings,
) -> ResponseUxScopeSettings:
    if raw_scope is None:
        return defaults
    if not isinstance(raw_scope, dict):
        raise _config_error("config_table_invalid", table=field_prefix, value_type=type(raw_scope).__name__)
    settings = ResponseUxScopeSettings(
        reaction=_coerce_bool(raw_scope.get("reaction", defaults.reaction), field_name=f"{field_prefix}.reaction"),
        typing=_coerce_bool(raw_scope.get("typing", defaults.typing), field_name=f"{field_prefix}.typing"),
        progress=_coerce_bool(raw_scope.get("progress", defaults.progress), field_name=f"{field_prefix}.progress"),
        stream=_coerce_bool(raw_scope.get("stream", defaults.stream), field_name=f"{field_prefix}.stream"),
    )
    settings.validate(field_prefix=field_prefix)
    return settings


def _parse_response_ux_settings(data: dict[str, Any]) -> ResponseUxSettings:
    defaults = default_response_ux_settings()
    raw_ux = data.get("response_ux")
    if raw_ux is None:
        return defaults
    if not isinstance(raw_ux, dict):
        raise _config_error("config_table_invalid", table="response_ux", value_type=type(raw_ux).__name__)
    return ResponseUxSettings(
        private_chat=_parse_response_ux_scope(
            raw_ux.get("private_chat"),
            field_prefix="response_ux.private_chat",
            defaults=defaults.private_chat,
        ),
        group_chat=_parse_response_ux_scope(
            raw_ux.get("group_chat"),
            field_prefix="response_ux.group_chat",
            defaults=defaults.group_chat,
        ),
    )


def _parse_admin_ids(raw_admin_ids: str) -> set[int]:
    admin_ids: set[int] = set()
    for raw_value in raw_admin_ids.split(","):
        stripped = raw_value.strip()
        if not stripped:
            continue
        try:
            admin_ids.add(int(stripped))
        except ValueError as exc:
            raise ConfigError(f"telegram_admin_ids_invalid: value={stripped!r}") from exc
    return admin_ids


def load_config(config_path: str | os.PathLike[str], env_path: str | os.PathLike[str] | None = None) -> AppConfig:
    path = Path(config_path)
    LOGGER.info("config_validation_started", extra={"extra_fields": {"config_path": str(path)}})
    if env_path:
        _load_dotenv(Path(env_path))
    else:
        _load_dotenv(path.parent / ".env")

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    token = _require_env("TELEGRAM_BOT_TOKEN")
    admin_ids = _parse_admin_ids(os.environ.get("TELEGRAM_ADMIN_IDS", ""))
    telegram_data = _require_table(data, "telegram")
    command_rule_groups = _parse_command_rule_groups(data)
    execution_profiles = _parse_execution_profiles(data, command_rule_groups=command_rule_groups)
    workspace_profile_defaults = _parse_workspace_profile_defaults(data, profiles=execution_profiles)
    admin_only = _parse_admin_only_settings(data)
    response_ux = _parse_response_ux_settings(data)
    break_glass_ttl_seconds = _coerce_int(
        data.get("break_glass_ttl_seconds", 1800),
        field_name="break_glass_ttl_seconds",
        minimum=60,
        maximum=86_400,
    )

    config = AppConfig(
        bot_name=str(data.get("bot_name", "Codex Gateway")),
        telegram_api_base=str(data.get("telegram_api_base", "https://api.telegram.org")),
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
        codex_auth_source_home=Path(data["codex_auth_source_home"]) if str(data.get("codex_auth_source_home", "")).strip() else None,
        default_workspace_name=(str(data.get("default_workspace_name", "")).strip() or None),
        default_model=(str(data.get("default_model", "")).strip() or None),
        default_sandbox_mode=execution_profiles["default"].sandbox_mode,
        default_approval_policy=execution_profiles["default"].approval_policy,
        default_network_mode=execution_profiles["default"].network_mode,
        session_idle_ttl_seconds=int(data.get("session_idle_ttl_seconds", 21600)),
        command_timeout_seconds=int(data.get("command_timeout_seconds", 1800)),
        process_kill_grace_seconds=int(data.get("process_kill_grace_seconds", 8)),
        max_parallel_processes=int(data.get("max_parallel_processes", 2)),
        max_queue_per_workspace=int(data.get("max_queue_per_workspace", 8)),
        max_active_workspaces=int(data.get("max_active_workspaces", 16)),
        per_user_rate_limit_window_seconds=int(data.get("per_user_rate_limit_window_seconds", 20)),
        per_user_rate_limit_max_messages=int(data.get("per_user_rate_limit_max_messages", 6)),
        trusted_admin_only_bind=admin_only.bind,
        allowed_roots=[Path(p) for p in data.get("allowed_roots", [])],
        project_alias_roots=[Path(p) for p in data.get("project_alias_roots", [])],
        workspace_defaults={str(k): str(v) for k, v in data.get("workspace_defaults", {}).items()},
        workspace_profile_defaults=workspace_profile_defaults,
        execution_profiles=execution_profiles,
        command_rule_groups=command_rule_groups,
        admin_only=admin_only,
        break_glass_ttl_seconds=break_glass_ttl_seconds,
        telegram=TelegramSettings(
            allow_private_chats=bool(telegram_data.get("allow_private_chats", True)),
            allow_group_chats=bool(telegram_data.get("allow_group_chats", True)),
            allow_topics=bool(telegram_data.get("allow_topics", True)),
        ),
        response_ux=response_ux,
    )
    LOGGER.info(
        "config_validation_succeeded",
        extra={
            "extra_fields": {
                "config_path": str(path),
                "profile_count": len(config.execution_profiles),
                "rule_group_count": len(config.command_rule_groups),
                "workspace_profile_default_count": len(config.workspace_profile_defaults),
            }
        },
    )
    return config
