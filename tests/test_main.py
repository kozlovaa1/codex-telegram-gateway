from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from codex_telegram_gateway.config import AdminOnlySettings, AppConfig, ExecutionProfile, TelegramSettings, default_response_ux_settings


def make_config(tmp: str) -> AppConfig:
    profiles = {
        "default": ExecutionProfile("default", "workspace-write", "never", "restricted", "default", False),
        "ops": ExecutionProfile("ops", "workspace-write", "on-request", "restricted", "ops", True),
        "break-glass": ExecutionProfile("break-glass", "danger-full-access", "never", "enabled", "break-glass", True),
    }
    return AppConfig(
        bot_name="test",
        telegram_api_base="https://api.telegram.org",
        telegram_token="token",
        telegram_admin_ids={1},
        poll_timeout_seconds=10,
        poll_retry_delay_seconds=1,
        telegram_message_chunk=3900,
        stream_edit_interval_seconds=2.0,
        status_port=8085,
        sqlite_path=Path(tmp) / "db.sqlite3",
        runtime_dir=Path(tmp) / "runtime",
        log_dir=Path(tmp) / "log",
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
        allowed_roots=[Path(tmp)],
        project_alias_roots=[Path(tmp)],
        workspace_defaults={"server-ops": tmp},
        workspace_profile_defaults={},
        execution_profiles=profiles,
        command_rule_groups={"default": ("workspace-safe",), "ops": ("workspace-safe",), "break-glass": ("workspace-safe",)},
        admin_only=AdminOnlySettings(True, False, True, True, True, True),
        break_glass_ttl_seconds=1800,
        telegram=TelegramSettings(True, True, True),
        response_ux=default_response_ux_settings(),
    )


class MainBootstrapTests(unittest.TestCase):
    def test_main_wires_policy_and_preflight_into_session_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            logger = MagicMock()
            store_instance = MagicMock()
            telegram_instance = MagicMock()
            adapter_instance = MagicMock()
            session_instance = MagicMock()
            app_instance = MagicMock()

            with patch("codex_telegram_gateway.__main__.argparse.ArgumentParser.parse_args", return_value=Namespace(config="config.toml", env_file=".env")):
                with patch("codex_telegram_gateway.__main__.load_config", return_value=config):
                    with patch("codex_telegram_gateway.__main__.setup_logging", return_value=logger):
                        with patch("codex_telegram_gateway.__main__.WorkspaceStore", return_value=store_instance) as workspace_store_cls:
                            with patch("codex_telegram_gateway.__main__.TelegramApi", return_value=telegram_instance):
                                with patch("codex_telegram_gateway.__main__.CodexAdapter", return_value=adapter_instance):
                                    with patch("codex_telegram_gateway.__main__.SessionManager", return_value=session_instance) as session_manager_cls:
                                        with patch("codex_telegram_gateway.__main__.GatewayApp", return_value=app_instance) as gateway_app_cls:
                                            with patch("codex_telegram_gateway.__main__.asyncio.run") as asyncio_run:
                                                from codex_telegram_gateway import __main__

                                                __main__.main()

            workspace_store_cls.assert_called_once()
            session_manager_cls.assert_called_once()
            kwargs = session_manager_cls.call_args.kwargs
            self.assertIn("policy_resolver", kwargs)
            self.assertIn("preflight_checker", kwargs)
            app_kwargs = gateway_app_cls.call_args.kwargs
            self.assertIn("response_ux", app_kwargs)
            store_instance.initialize.assert_called_once()
            asyncio_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
