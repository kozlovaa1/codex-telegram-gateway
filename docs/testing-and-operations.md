[← Previous Page](architecture.md) · [Back to README](../README.md)

# Testing and Operations

This page covers test execution, log inspection, deployment notes, and common operator checks.

## Run Tests

```bash
cd <project_dir>
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Current test coverage includes:

- config validation
- path security
- workspace store behavior and migrations
- execution policy resolution
- session manager lifecycle
- workspace preflight
- app default workspace and session controls
- prompt flow and response UX behavior
- Telegram transport fallbacks

## Logs

The gateway writes structured JSON logs under `log_dir`, typically to `gateway.log`.

Useful events include:

- `policy_services_bootstrapped`
- `response_ux_bootstrapped`
- `workspace_store_migration_started`
- `workspace_store_migration_finished`
- `session_start`
- `session_stop`
- `session_restart`
- `workspace_busy_conflict`
- `break_glass_enabled`
- `preflight_failed`
- `command_rule_violation`

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Bot does not respond | `systemctl status`, `journalctl`, Telegram token, poll errors |
| Immediate startup failure | `config.toml`, `.env`, runtime path permissions |
| Bind rejected | `allowed_roots`, canonical target path, admin permissions |
| Run blocked before start | preflight result, service-user filesystem access |
| Session/profile change rejected | `TELEGRAM_ADMIN_IDS`, `[admin_only]`, workspace busy state |
| Codex auth missing | `OPENAI_API_KEY` or readable `codex_auth_source_home/auth.json` |
| Missing dynamic aliases | `project_alias_roots` existence and directory structure |

## `systemd` Notes

The repository unit is a template, not a universal drop-in.

Before enabling it:

- replace `User=` and `Group=`
- replace placeholder project, runtime, log, and allowed-root paths
- ensure the service user can write `runtime_dir` and `log_dir`
- ensure the service user can execute `codex_bin`

Typical deployment flow:

```bash
cd <project_dir>
.venv/bin/pip install -e .
sudo systemctl restart codex-telegram-gateway
sudo systemctl status codex-telegram-gateway
```

## Operational Constraints

- no webhook mode yet
- no interactive approval flow through Telegram
- sandbox behavior still depends on real filesystem permissions
- topic-based private-chat behavior requires BotFather threaded mode

## See Also

- [Getting Started](getting-started.md)
- [Configuration](configuration.md)
- [Architecture](architecture.md)
