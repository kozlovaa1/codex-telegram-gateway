from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from codex_telegram_gateway.config import ConfigError, load_config


class LoadConfigTests(unittest.TestCase):
    def test_load_config_builds_execution_profiles(self) -> None:
        config = self._load(
            """
            sqlite_path = "/tmp/gateway.sqlite3"
            runtime_dir = "/tmp/runtime"
            log_dir = "/tmp/log"
            codex_bin = "/bin/true"
            default_sandbox_mode = "workspace-write"
            default_approval_policy = "never"
            default_network_mode = "restricted"
            break_glass_ttl_seconds = 1200
            allowed_roots = ["/srv/projects"]
            project_alias_roots = ["/srv/projects"]
            default_workspace_name = "server-ops"

            [workspace_defaults]
            server-ops = "/srv/projects"

            [workspace_profile_defaults]
            server-ops = "default"
            infra = "ops"

            [command_rule_groups]
            default = ["workspace-safe"]
            ops = ["workspace-safe", "ops-write"]
            break-glass = ["workspace-safe", "break-glass"]

            [execution_profiles.ops]
            approval_policy = "on-request"
            command_rule_group = "ops"
            admin_only = true

            [admin_only]
            bind = true
            use = false
            execmode = true
            approvals = true
            break_glass = true
            command_rule_overrides = true

            [telegram]
            allow_private_chats = true
            allow_group_chats = true
            allow_topics = true
            """
        )

        self.assertEqual(config.default_network_mode, "restricted")
        self.assertEqual(config.execution_profiles["default"].sandbox_mode, "workspace-write")
        self.assertEqual(config.workspace_profile_defaults["infra"], "ops")
        self.assertTrue(config.admin_only.break_glass)
        self.assertEqual(config.break_glass_ttl_seconds, 1200)

    def test_load_config_rejects_unknown_rule_group_reference(self) -> None:
        with self.assertRaises(ConfigError):
            self._load(
                """
                sqlite_path = "/tmp/gateway.sqlite3"
                runtime_dir = "/tmp/runtime"
                log_dir = "/tmp/log"
                codex_bin = "/bin/true"

                [workspace_defaults]
                server-ops = "/srv/projects"

                [execution_profiles.ops]
                command_rule_group = "missing"

                [telegram]
                allow_private_chats = true
                allow_group_chats = true
                allow_topics = true
                """
            )

    def test_load_config_rejects_unknown_workspace_profile(self) -> None:
        with self.assertRaises(ConfigError):
            self._load(
                """
                sqlite_path = "/tmp/gateway.sqlite3"
                runtime_dir = "/tmp/runtime"
                log_dir = "/tmp/log"
                codex_bin = "/bin/true"

                [workspace_defaults]
                server-ops = "/srv/projects"

                [workspace_profile_defaults]
                server-ops = "missing"

                [telegram]
                allow_private_chats = true
                allow_group_chats = true
                allow_topics = true
                """
            )

    def test_load_config_rejects_invalid_break_glass_ttl(self) -> None:
        with self.assertRaises(ConfigError):
            self._load(
                """
                sqlite_path = "/tmp/gateway.sqlite3"
                runtime_dir = "/tmp/runtime"
                log_dir = "/tmp/log"
                codex_bin = "/bin/true"
                break_glass_ttl_seconds = 10

                [workspace_defaults]
                server-ops = "/srv/projects"

                [telegram]
                allow_private_chats = true
                allow_group_chats = true
                allow_topics = true
                """
            )

    def test_load_config_rejects_unsafe_default_network_mode(self) -> None:
        with self.assertRaises(ConfigError):
            self._load(
                """
                sqlite_path = "/tmp/gateway.sqlite3"
                runtime_dir = "/tmp/runtime"
                log_dir = "/tmp/log"
                codex_bin = "/bin/true"
                default_network_mode = "enabled"

                [workspace_defaults]
                server-ops = "/srv/projects"

                [telegram]
                allow_private_chats = true
                allow_group_chats = true
                allow_topics = true
                """
            )

    def _load(self, config_body: str):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            env_path = Path(tmp) / ".env"
            config_path.write_text(textwrap.dedent(config_body).strip() + "\n", encoding="utf-8")
            env_path.write_text("TELEGRAM_BOT_TOKEN=test-token\nTELEGRAM_ADMIN_IDS=1,2\n", encoding="utf-8")
            previous_token = os.environ.get("TELEGRAM_BOT_TOKEN")
            previous_admin_ids = os.environ.get("TELEGRAM_ADMIN_IDS")
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_ADMIN_IDS", None)
            try:
                return load_config(config_path, env_path)
            finally:
                self._restore_env("TELEGRAM_BOT_TOKEN", previous_token)
                self._restore_env("TELEGRAM_ADMIN_IDS", previous_admin_ids)

    def _restore_env(self, name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
            return
        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
