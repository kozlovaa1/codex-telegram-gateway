from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path

from codex_telegram_gateway.config import AdminOnlySettings, AppConfig, ExecutionProfile, TelegramSettings
from codex_telegram_gateway.execution_policy import ExecutionPolicyResolver, PolicyOverride
from codex_telegram_gateway.models import ExecutionPolicyRecord


def make_config() -> AppConfig:
    profiles = {
        "default": ExecutionProfile(
            name="default",
            sandbox_mode="workspace-write",
            approval_policy="never",
            network_mode="restricted",
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
    return AppConfig(
        bot_name="test",
        telegram_api_base="https://api.telegram.org",
        telegram_token="token",
        telegram_admin_ids={7},
        poll_timeout_seconds=10,
        poll_retry_delay_seconds=1,
        telegram_message_chunk=3900,
        stream_edit_interval_seconds=2.0,
        status_port=8085,
        sqlite_path=Path("/tmp/gateway.sqlite3"),
        runtime_dir=Path("/tmp/runtime"),
        log_dir=Path("/tmp/log"),
        codex_bin="/bin/true",
        codex_auth_source_home=None,
        default_workspace_name="server-ops",
        default_model=None,
        default_sandbox_mode="workspace-write",
        default_approval_policy="never",
        default_network_mode="restricted",
        session_idle_ttl_seconds=60,
        command_timeout_seconds=60,
        process_kill_grace_seconds=1,
        max_parallel_processes=1,
        max_queue_per_workspace=1,
        max_active_workspaces=4,
        per_user_rate_limit_window_seconds=10,
        per_user_rate_limit_max_messages=5,
        trusted_admin_only_bind=True,
        allowed_roots=[Path("/srv/projects")],
        project_alias_roots=[Path("/srv/projects")],
        workspace_defaults={"server-ops": "/srv/projects"},
        workspace_profile_defaults={"infra": "ops", "path:/srv/infra": "ops"},
        execution_profiles=profiles,
        command_rule_groups={
            "default": ("workspace-safe",),
            "ops": ("workspace-safe", "ops-write"),
            "break-glass": ("workspace-safe", "ops-write", "break-glass"),
        },
        admin_only=AdminOnlySettings(
            bind=True,
            use=False,
            execmode=True,
            approvals=True,
            break_glass=True,
            command_rule_overrides=True,
        ),
        break_glass_ttl_seconds=1800,
        telegram=TelegramSettings(True, True, True),
    )


class ExecutionPolicyResolverTests(unittest.TestCase):
    def test_workspace_default_applies_when_no_durable_override(self) -> None:
        resolver = ExecutionPolicyResolver(make_config())
        stored = ExecutionPolicyRecord(
            workspace_name="infra",
            profile_name="default",
            override_scope="profile-default",
            sandbox_mode="workspace-write",
            approval_policy="never",
            network_mode="restricted",
            command_rule_set_version=1,
            break_glass_expires_at=None,
            created_at="2026-04-20T10:00:00Z",
            updated_at="2026-04-20T10:00:00Z",
        )

        resolved = resolver.resolve(
            workspace_name="infra",
            workspace_path="/srv/infra",
            user_id=7,
            stored_policy=stored,
        )

        self.assertEqual(resolved.profile_name, "ops")
        self.assertEqual(resolved.command_rule_group, "ops")
        self.assertFalse(resolved.durable_override_applied)

    def test_durable_override_wins_over_workspace_default(self) -> None:
        resolver = ExecutionPolicyResolver(make_config())
        stored = ExecutionPolicyRecord(
            workspace_name="infra",
            profile_name="default",
            override_scope="durable-override",
            sandbox_mode="read-only",
            approval_policy="never",
            network_mode="restricted",
            command_rule_set_version=2,
            break_glass_expires_at=None,
            created_at="2026-04-20T10:00:00Z",
            updated_at="2026-04-20T10:10:00Z",
        )

        resolved = resolver.resolve(
            workspace_name="infra",
            workspace_path="/srv/infra",
            user_id=7,
            stored_policy=stored,
        )

        self.assertEqual(resolved.profile_name, "default")
        self.assertEqual(resolved.sandbox_mode, "read-only")
        self.assertTrue(resolved.durable_override_applied)
        self.assertEqual(resolved.command_rule_set_version, 2)

    def test_break_glass_overlays_durable_policy_until_expiry(self) -> None:
        resolver = ExecutionPolicyResolver(make_config())
        stored = ExecutionPolicyRecord(
            workspace_name="infra",
            profile_name="default",
            override_scope="durable-override",
            sandbox_mode="read-only",
            approval_policy="never",
            network_mode="restricted",
            command_rule_set_version=2,
            break_glass_expires_at="2026-04-20T12:30:00Z",
            created_at="2026-04-20T10:00:00Z",
            updated_at="2026-04-20T10:10:00Z",
        )

        resolved = resolver.resolve(
            workspace_name="infra",
            workspace_path="/srv/infra",
            user_id=7,
            stored_policy=stored,
            now=datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
        )

        self.assertTrue(resolved.break_glass_active)
        self.assertEqual(resolved.profile_name, "break-glass")
        self.assertEqual(resolved.network_mode, "enabled")

    def test_one_shot_override_applies_last(self) -> None:
        resolver = ExecutionPolicyResolver(make_config())
        stored = ExecutionPolicyRecord(
            workspace_name="server-ops",
            profile_name="default",
            override_scope="profile-default",
            sandbox_mode="workspace-write",
            approval_policy="never",
            network_mode="restricted",
            command_rule_set_version=1,
            break_glass_expires_at=None,
            created_at="2026-04-20T10:00:00Z",
            updated_at="2026-04-20T10:00:00Z",
        )

        resolved = resolver.resolve(
            workspace_name="server-ops",
            workspace_path="/srv/projects",
            user_id=7,
            stored_policy=stored,
            one_shot_override=PolicyOverride(approval_policy="untrusted", reason="approvals"),
        )

        self.assertEqual(resolved.approval_policy, "untrusted")
        self.assertIn("one-shot override:approvals", resolved.sources)

    def test_non_admin_gets_admin_required_signal_for_privileged_profile(self) -> None:
        resolver = ExecutionPolicyResolver(make_config())
        stored = ExecutionPolicyRecord(
            workspace_name="infra",
            profile_name="default",
            override_scope="profile-default",
            sandbox_mode="workspace-write",
            approval_policy="never",
            network_mode="restricted",
            command_rule_set_version=1,
            break_glass_expires_at=None,
            created_at="2026-04-20T10:00:00Z",
            updated_at="2026-04-20T10:00:00Z",
        )

        resolved = resolver.resolve(
            workspace_name="infra",
            workspace_path="/srv/infra",
            user_id=100,
            stored_policy=stored,
        )

        self.assertTrue(resolved.admin_required)
        self.assertFalse(resolved.user_is_admin)


if __name__ == "__main__":
    unittest.main()
