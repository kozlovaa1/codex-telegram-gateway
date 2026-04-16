from __future__ import annotations

import argparse
import asyncio

from .app import GatewayApp
from .codex_adapter import CodexAdapter
from .config import load_config
from .logging_utils import setup_logging
from .session_manager import SessionManager
from .telegram_api import TelegramApi
from .workspace_store import WorkspaceStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    config = load_config(args.config, args.env_file)
    logger = setup_logging(config.log_dir)
    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    store = WorkspaceStore(
        sqlite_path=config.sqlite_path,
        defaults=config.workspace_defaults,
        default_model=config.default_model,
        default_sandbox_mode=config.default_sandbox_mode,
        default_approval_policy=config.default_approval_policy,
    )
    store.initialize()
    telegram = TelegramApi(config.telegram_token, config.telegram_api_base)
    adapter = CodexAdapter(
        codex_bin=config.codex_bin,
        runtime_dir=config.runtime_dir,
        timeout_seconds=config.command_timeout_seconds,
        kill_grace_seconds=config.process_kill_grace_seconds,
        auth_source_home=config.codex_auth_source_home,
    )
    sessions = SessionManager(
        store=store,
        adapter=adapter,
        logger=logger,
        stream_edit_interval_seconds=config.stream_edit_interval_seconds,
        session_idle_ttl_seconds=config.session_idle_ttl_seconds,
        max_active_workspaces=config.max_active_workspaces,
        max_parallel_processes=config.max_parallel_processes,
        max_queue_per_workspace=config.max_queue_per_workspace,
    )
    app = GatewayApp(config, store, sessions, telegram, logger)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
