from __future__ import annotations

import argparse
import asyncio
import logging

from .app import GatewayApp
from .codex_adapter import CodexAdapter
from .config import load_config
from .execution_policy import ExecutionPolicyResolver
from .logging_utils import setup_logging
from .response_ux import ResponseUxCoordinator
from .session_manager import SessionManager
from .telegram_api import TelegramApi
from .workspace_preflight import WorkspacePreflightChecker
from .workspace_store import WorkspaceStore


def _build_component[T](logger: logging.Logger, component_name: str, factory) -> T:
    try:
        return factory()
    except Exception as exc:
        logger.error(
            "startup_dependency_wiring_failed",
            extra={"extra_fields": {"component": component_name, "reason": str(exc)}},
            exc_info=True,
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    config = load_config(args.config, args.env_file)
    logger = setup_logging(config.log_dir)
    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    policy_resolver = _build_component(
        logger,
        "ExecutionPolicyResolver",
        lambda: ExecutionPolicyResolver(config),
    )
    preflight_checker = _build_component(
        logger,
        "WorkspacePreflightChecker",
        lambda: WorkspacePreflightChecker(config.allowed_roots),
    )
    logger.info(
        "policy_services_bootstrapped",
        extra={
            "extra_fields": {
                "profiles": sorted(config.execution_profiles),
                "workspace_profile_defaults": len(config.workspace_profile_defaults),
                "preflight_checker": "enabled",
            }
        },
    )
    store = _build_component(
        logger,
        "WorkspaceStore",
        lambda: WorkspaceStore(
            sqlite_path=config.sqlite_path,
            defaults=config.workspace_defaults,
            default_model=config.default_model,
            default_sandbox_mode=config.default_sandbox_mode,
            default_approval_policy=config.default_approval_policy,
            default_network_mode=config.default_network_mode,
        ),
    )
    _build_component(logger, "WorkspaceStore.initialize", store.initialize)
    telegram = _build_component(
        logger,
        "TelegramApi",
        lambda: TelegramApi(config.telegram_token, config.telegram_api_base),
    )
    response_ux = _build_component(
        logger,
        "ResponseUxCoordinator",
        lambda: ResponseUxCoordinator(config, telegram, logger),
    )
    adapter = _build_component(
        logger,
        "CodexAdapter",
        lambda: CodexAdapter(
            codex_bin=config.codex_bin,
            runtime_dir=config.runtime_dir,
            timeout_seconds=config.command_timeout_seconds,
            kill_grace_seconds=config.process_kill_grace_seconds,
            auth_source_home=config.codex_auth_source_home,
        ),
    )
    sessions = _build_component(
        logger,
        "SessionManager",
        lambda: SessionManager(
            store=store,
            adapter=adapter,
            logger=logger,
            stream_edit_interval_seconds=config.stream_edit_interval_seconds,
            session_idle_ttl_seconds=config.session_idle_ttl_seconds,
            max_active_workspaces=config.max_active_workspaces,
            max_parallel_processes=config.max_parallel_processes,
            max_queue_per_workspace=config.max_queue_per_workspace,
            policy_resolver=policy_resolver,
            preflight_checker=preflight_checker,
        ),
    )
    app = _build_component(
        logger,
        "GatewayApp",
        lambda: GatewayApp(
            config,
            store,
            sessions,
            telegram,
            logger,
            policy_resolver=policy_resolver,
            response_ux=response_ux,
        ),
    )
    logger.info(
        "response_ux_bootstrapped",
        extra={
            "extra_fields": {
                "private_scope": {
                    "reaction": config.response_ux.private_chat.reaction,
                    "typing": config.response_ux.private_chat.typing,
                    "progress": config.response_ux.private_chat.progress,
                    "stream": config.response_ux.private_chat.stream,
                },
                "group_scope": {
                    "reaction": config.response_ux.group_chat.reaction,
                    "typing": config.response_ux.group_chat.typing,
                    "progress": config.response_ux.group_chat.progress,
                    "stream": config.response_ux.group_chat.stream,
                },
            }
        },
    )
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
