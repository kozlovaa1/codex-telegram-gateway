from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from .config import AppConfig, ExecutionProfile
from .models import ExecutionPolicyRecord


LOGGER = logging.getLogger("codex_telegram_gateway.execution_policy")


@dataclass(frozen=True, slots=True)
class PolicyOverride:
    profile_name: str | None = None
    sandbox_mode: str | None = None
    approval_policy: str | None = None
    network_mode: str | None = None
    command_rule_group: str | None = None
    command_rule_set_version: int | None = None
    reason: str = "one-shot"


@dataclass(frozen=True, slots=True)
class PolicyMutation:
    profile_name: str
    override_scope: str
    sandbox_mode: str
    approval_policy: str
    network_mode: str
    command_rule_set_version: int
    break_glass_expires_at: str | None


@dataclass(frozen=True, slots=True)
class ResolvedExecutionPolicy:
    workspace_name: str
    workspace_path: str
    profile_name: str
    sandbox_mode: str
    approval_policy: str
    network_mode: str
    command_rule_group: str
    command_rules: tuple[str, ...]
    command_rule_set_version: int
    break_glass_expires_at: str | None
    break_glass_active: bool
    user_is_admin: bool
    admin_required: bool
    workspace_default_profile_name: str | None
    durable_override_applied: bool
    sources: tuple[str, ...]


class PolicyAuthorizationError(RuntimeError):
    pass


class ExecutionPolicyResolver:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def resolve(
        self,
        *,
        workspace_name: str,
        workspace_path: str,
        user_id: int,
        stored_policy: ExecutionPolicyRecord | None,
        one_shot_override: PolicyOverride | None = None,
        now: datetime | None = None,
    ) -> ResolvedExecutionPolicy:
        current_time = now or datetime.now(UTC)
        user_is_admin = user_id in self.config.telegram_admin_ids
        base_profile = self.config.execution_profiles["default"]
        profile = base_profile
        sources: list[str] = ["profile default"]
        workspace_default_profile_name = self._workspace_default_profile_name(workspace_name, workspace_path)
        if workspace_default_profile_name:
            profile = self.config.execution_profiles[workspace_default_profile_name]
            sources.append(f"workspace default:{workspace_default_profile_name}")

        effective_profile_name = profile.name
        sandbox_mode = profile.sandbox_mode
        approval_policy = profile.approval_policy
        network_mode = profile.network_mode
        command_rule_group = profile.command_rule_group
        command_rule_set_version = stored_policy.command_rule_set_version if stored_policy else 1
        break_glass_expires_at = stored_policy.break_glass_expires_at if stored_policy else None
        durable_override_applied = False

        if stored_policy and stored_policy.override_scope == "durable-override":
            effective_profile_name = stored_policy.profile_name
            sandbox_mode = stored_policy.sandbox_mode
            approval_policy = stored_policy.approval_policy
            network_mode = stored_policy.network_mode
            command_rule_group = self._command_rule_group_for_profile(stored_policy.profile_name, fallback=command_rule_group)
            command_rule_set_version = stored_policy.command_rule_set_version
            durable_override_applied = True
            sources.append("durable workspace override")

        break_glass_active = self._is_break_glass_active(break_glass_expires_at, current_time)
        if break_glass_active:
            break_glass_profile = self.config.execution_profiles["break-glass"]
            effective_profile_name = break_glass_profile.name
            sandbox_mode = break_glass_profile.sandbox_mode
            approval_policy = break_glass_profile.approval_policy
            network_mode = break_glass_profile.network_mode
            command_rule_group = break_glass_profile.command_rule_group
            sources.append("temporary break-glass override")
            LOGGER.info(
                "break_glass_active",
                extra={
                    "extra_fields": {
                        "workspace_name": workspace_name,
                        "workspace_path": workspace_path,
                        "expires_at": break_glass_expires_at,
                    }
                },
            )

        if one_shot_override:
            if one_shot_override.profile_name:
                override_profile = self.config.execution_profiles[one_shot_override.profile_name]
                effective_profile_name = override_profile.name
                sandbox_mode = override_profile.sandbox_mode
                approval_policy = override_profile.approval_policy
                network_mode = override_profile.network_mode
                command_rule_group = override_profile.command_rule_group
            if one_shot_override.sandbox_mode is not None:
                sandbox_mode = one_shot_override.sandbox_mode
            if one_shot_override.approval_policy is not None:
                approval_policy = one_shot_override.approval_policy
            if one_shot_override.network_mode is not None:
                network_mode = one_shot_override.network_mode
            if one_shot_override.command_rule_group is not None:
                command_rule_group = one_shot_override.command_rule_group
            if one_shot_override.command_rule_set_version is not None:
                command_rule_set_version = one_shot_override.command_rule_set_version
            sources.append(f"one-shot override:{one_shot_override.reason}")

        profile_for_admin = self.config.execution_profiles.get(effective_profile_name)
        admin_required = profile_for_admin.admin_only if profile_for_admin else False
        if admin_required and not user_is_admin:
            LOGGER.warning(
                "admin_denied_elevation",
                extra={
                    "extra_fields": {
                        "workspace_name": workspace_name,
                        "workspace_path": workspace_path,
                        "user_id": user_id,
                        "requested_profile": effective_profile_name,
                    }
                },
            )

        LOGGER.debug(
            "policy_resolved",
            extra={
                "extra_fields": {
                    "workspace_name": workspace_name,
                    "workspace_path": workspace_path,
                    "user_id": user_id,
                    "stored_profile_name": stored_policy.profile_name if stored_policy else None,
                    "stored_override_scope": stored_policy.override_scope if stored_policy else None,
                    "selected_profile_name": effective_profile_name,
                    "workspace_default_profile_name": workspace_default_profile_name,
                }
            },
        )
        return ResolvedExecutionPolicy(
            workspace_name=workspace_name,
            workspace_path=workspace_path,
            profile_name=effective_profile_name,
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
            network_mode=network_mode,
            command_rule_group=command_rule_group,
            command_rules=self.config.command_rule_groups[command_rule_group],
            command_rule_set_version=command_rule_set_version,
            break_glass_expires_at=break_glass_expires_at,
            break_glass_active=break_glass_active,
            user_is_admin=user_is_admin,
            admin_required=admin_required,
            workspace_default_profile_name=workspace_default_profile_name,
            durable_override_applied=durable_override_applied,
            sources=tuple(sources),
        )

    def authorize_command(
        self,
        *,
        command_name: str,
        user_id: int,
        workspace_name: str | None = None,
        workspace_path: str | None = None,
        requested_profile_name: str | None = None,
        requested_approval_policy: str | None = None,
        requested_command_rule_group: str | None = None,
    ) -> None:
        user_is_admin = user_id in self.config.telegram_admin_ids
        requires_admin = False
        if command_name == "bind":
            requires_admin = self.config.admin_only.bind
        elif command_name == "use":
            requires_admin = self.config.admin_only.use
        elif command_name == "execmode" and requested_profile_name is not None:
            requires_admin = self.config.admin_only.execmode
        elif command_name == "approvals" and requested_approval_policy is not None:
            requires_admin = self.config.admin_only.approvals
        elif command_name == "break_glass":
            requires_admin = self.config.admin_only.break_glass
        elif requested_command_rule_group is not None:
            requires_admin = self.config.admin_only.command_rule_overrides

        if requested_profile_name is not None:
            profile = self.config.execution_profiles.get(requested_profile_name)
            if profile is not None and profile.admin_only:
                requires_admin = True

        if requires_admin and not user_is_admin:
            LOGGER.warning(
                "admin_denied",
                extra={
                    "extra_fields": {
                        "user_id": user_id,
                        "command": command_name,
                        "workspace_name": workspace_name,
                        "workspace_path": workspace_path,
                        "requested_profile": requested_profile_name,
                        "requested_approval_policy": requested_approval_policy,
                        "requested_command_rule_group": requested_command_rule_group,
                    }
                },
            )
            raise PolicyAuthorizationError("Admin only.")

        if requires_admin and user_is_admin:
            LOGGER.info(
                "privileged_transition_allowed",
                extra={
                    "extra_fields": {
                        "user_id": user_id,
                        "command": command_name,
                        "workspace_name": workspace_name,
                        "workspace_path": workspace_path,
                        "requested_profile": requested_profile_name,
                        "requested_approval_policy": requested_approval_policy,
                        "requested_command_rule_group": requested_command_rule_group,
                    }
                },
            )

    def make_durable_override(
        self,
        current: ResolvedExecutionPolicy,
        *,
        profile_name: str | None = None,
        sandbox_mode: str | None = None,
        approval_policy: str | None = None,
        network_mode: str | None = None,
    ) -> PolicyMutation:
        next_profile_name = profile_name or current.profile_name
        command_rule_group = self._command_rule_group_for_profile(next_profile_name, fallback=current.command_rule_group)
        return PolicyMutation(
            profile_name=next_profile_name,
            override_scope="durable-override",
            sandbox_mode=sandbox_mode or current.sandbox_mode,
            approval_policy=approval_policy or current.approval_policy,
            network_mode=network_mode or current.network_mode,
            command_rule_set_version=current.command_rule_set_version,
            break_glass_expires_at=current.break_glass_expires_at,
        )

    def clear_durable_override(
        self,
        *,
        workspace_name: str,
        workspace_path: str,
        current: ResolvedExecutionPolicy,
    ) -> PolicyMutation:
        profile_name = current.workspace_default_profile_name or "default"
        profile = self.config.execution_profiles[profile_name]
        return PolicyMutation(
            profile_name=profile.name,
            override_scope="profile-default",
            sandbox_mode=profile.sandbox_mode,
            approval_policy=profile.approval_policy,
            network_mode=profile.network_mode,
            command_rule_set_version=current.command_rule_set_version,
            break_glass_expires_at=current.break_glass_expires_at,
        )

    def activate_break_glass(self, current: ResolvedExecutionPolicy, *, expires_at: str) -> PolicyMutation:
        return PolicyMutation(
            profile_name=current.profile_name,
            override_scope="durable-override" if current.durable_override_applied else "profile-default",
            sandbox_mode=current.sandbox_mode,
            approval_policy=current.approval_policy,
            network_mode=current.network_mode,
            command_rule_set_version=current.command_rule_set_version,
            break_glass_expires_at=expires_at,
        )

    def clear_break_glass(self, current: ResolvedExecutionPolicy) -> PolicyMutation:
        return PolicyMutation(
            profile_name=current.profile_name,
            override_scope="durable-override" if current.durable_override_applied else "profile-default",
            sandbox_mode=current.sandbox_mode,
            approval_policy=current.approval_policy,
            network_mode=current.network_mode,
            command_rule_set_version=current.command_rule_set_version,
            break_glass_expires_at=None,
        )

    def _workspace_default_profile_name(self, workspace_name: str, workspace_path: str) -> str | None:
        exact_key = f"workspace:{workspace_name}"
        if exact_key in self.config.workspace_profile_defaults:
            return self.config.workspace_profile_defaults[exact_key]
        if workspace_name in self.config.workspace_profile_defaults:
            return self.config.workspace_profile_defaults[workspace_name]

        best_match: tuple[int, str] | None = None
        for selector, profile_name in self.config.workspace_profile_defaults.items():
            if not selector.startswith("path:"):
                continue
            prefix = selector[5:]
            if workspace_path == prefix or workspace_path.startswith(prefix.rstrip("/") + "/"):
                match = (len(prefix), profile_name)
                if best_match is None or match[0] > best_match[0]:
                    best_match = match
        return best_match[1] if best_match else None

    def _command_rule_group_for_profile(self, profile_name: str, *, fallback: str) -> str:
        profile = self.config.execution_profiles.get(profile_name)
        if profile is None:
            return fallback
        return profile.command_rule_group

    def _is_break_glass_active(self, expires_at: str | None, current_time: datetime) -> bool:
        if not expires_at:
            return False
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return expiry > current_time.astimezone(UTC)
