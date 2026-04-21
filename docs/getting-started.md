[Back to README](../README.md) · [Next Page →](configuration.md)

# Getting Started

Install, configure, and run `codex-telegram-gateway` on a Linux host.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Linux with `systemd` | The repo ships a service unit template |
| Python 3.12+ | No external runtime dependencies beyond the standard library |
| `Codex CLI` | Must be installed and reachable by the service user |
| Telegram bot token | Created via `@BotFather` |
| OpenAI or Codex auth | Either `OPENAI_API_KEY` or readable `auth.json` fallback |
| Optional `npx` | Needed only if `.codex/config.toml` enables Context7 via `@upstash/context7-mcp` |

## Installation

```bash
cd <project_dir>
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.toml config.toml
```

Create the runtime directories referenced by `config.toml`:

```bash
sudo mkdir -p <state_dir>
sudo mkdir -p <log_dir>
sudo chown -R <service_user>:<service_group> <state_dir>
sudo chown -R <service_user>:<service_group> <log_dir>
```

## Environment Variables

Create `.env` next to `config.toml`:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_IDS=111111111,222222222
OPENAI_API_KEY=...
CONTEXT7_API_KEY=...
```

Notes:

- `TELEGRAM_BOT_TOKEN` is required.
- `TELEGRAM_ADMIN_IDS` controls admin-only Telegram commands.
- `OPENAI_API_KEY` is optional if you rely on Codex auth fallback.
- `CONTEXT7_API_KEY` is optional and only matters when using Context7 MCP.

## Auth Fallback

If `OPENAI_API_KEY` is not set, the gateway copies `auth.json` from `codex_auth_source_home` into its isolated runtime home before launching Codex.

Requirements:

- `codex_auth_source_home` must point at the source `.codex` directory.
- The service user must be able to read `codex_auth_source_home/auth.json`.
- The service user must be able to write under `runtime_dir`.

## Minimal Configuration Checklist

Set these values in `config.toml` before the first run:

- `sqlite_path`
- `runtime_dir`
- `log_dir`
- `codex_bin`
- `codex_auth_source_home`
- `allowed_roots`
- `workspace_defaults`
- `default_workspace_name`

See [Configuration](configuration.md) for field-by-field guidance.

## Run Locally

```bash
cd <project_dir>
PYTHONPATH=src .venv/bin/python3 -m codex_telegram_gateway --config config.toml --env-file .env
```

The console process:

- loads `.env` and `config.toml`
- initializes SQLite tables and default workspaces
- prepares the isolated runtime home
- starts Telegram long polling

## Telegram Setup

1. Create the bot in `@BotFather`.
2. Decide whether group privacy mode should stay enabled.
3. If you want threaded private-chat sessions, enable `Threaded mode` in `@BotFather`.
4. Add the bot to the target private chat or group.
5. Use `/workspaces`, `/bind`, and `/use` to connect Telegram scopes to workspaces.

## First Workspace Flow

```text
/workspaces
/bind infra /absolute/path/to/infra
/use infra
/status
```

Behavior notes:

- `/bind` validates the target path against `allowed_roots`.
- `/use` in a forum supergroup or threaded private chat creates a topic-scoped session workspace.
- Unbound chats can fall back to `default_workspace_name` if it is configured.

## Context7 MCP

The repo includes `.codex/config.toml` for project-local MCP configuration. If `CONTEXT7_API_KEY` is present and `npx` is available, Codex can use Context7 for up-to-date library and API documentation lookup.

## See Also

- [Configuration](configuration.md)
- [CLI and Telegram Commands](cli.md)
- [Testing and Operations](testing-and-operations.md)
