# Codex Telegram Gateway

Telegram gateway for `Codex CLI` with per-chat and per-topic workspace bindings, persisted session state, and gateway-side execution policy enforcement.

## Overview

`codex-telegram-gateway` runs as a small long-polling Python service. Each Telegram `chat_id + thread_id` resolves to a workspace binding, and each workspace keeps its own Codex session and policy state so context does not leak across unrelated chats, topics, or threads.

## Highlights

- Python 3.12+ and standard library only
- SQLite persistence for workspaces, bindings, policies, and session state
- One Codex session per workspace, resumed with `codex exec resume`
- Workspace path allowlist enforcement before any bind or run
- Gateway-owned execution profiles for sandbox, approvals, network mode, and command rules
- Topic-aware `/use` flow for forum supergroups and threaded private chats
- Response UX policy split between private chats and group/topic chats
- `systemd`-friendly deployment with isolated runtime home and auth fallback support

## Repository Layout

| Path | Purpose |
| --- | --- |
| `src/codex_telegram_gateway/` | Application package |
| `tests/` | Unit tests for gateway behavior |
| `systemd/` | Example service unit |
| `config.example.toml` | Runtime configuration template |
| `.codex/config.toml` | Project-local Codex MCP configuration |
| `AGENTS.md` | Project map for AI agents |

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.toml config.toml
```

Create `.env` with:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_IDS=111111111,222222222
OPENAI_API_KEY=...
```

Run the gateway:

```bash
PYTHONPATH=src .venv/bin/python3 -m codex_telegram_gateway --config config.toml --env-file .env
```

For a production-oriented setup, runtime path guidance, and auth fallback details, see [Getting Started](docs/getting-started.md).

## Documentation

| Page | Description |
| --- | --- |
| [Getting Started](docs/getting-started.md) | Install, configure, and start the gateway |
| [Configuration](docs/configuration.md) | `config.toml`, `.env`, execution profiles, and response UX |
| [CLI and Telegram Commands](docs/cli.md) | Bot commands, workspace flows, and topic behavior |
| [Architecture](docs/architecture.md) | Module boundaries, runtime flow, and persistence model |
| [Testing and Operations](docs/testing-and-operations.md) | Test commands, logging, troubleshooting, and `systemd` notes |

## Core Flow

1. Telegram sends updates through long polling.
2. The gateway resolves the chat or topic binding to a workspace.
3. The workspace session and effective execution policy are loaded from SQLite.
4. The gateway performs path and filesystem preflight checks.
5. A separate `codex exec` or `codex exec resume` subprocess runs inside the selected workspace.
6. Response UX policy controls whether Telegram receives reactions, typing, progress, streaming, or final-only delivery.

## Security Model

- Workspace paths must resolve under configured `allowed_roots`.
- Session isolation is preserved per workspace and must not be shared across unrelated chats.
- Admin-only controls can be enforced independently for `bind`, `use`, legacy overrides, and break-glass transitions.
- Network mode and approvals are treated as gateway policy even when Codex CLI cannot express them as direct flags.

## Development

Run the test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Before enabling the example `systemd` unit, replace environment-specific `User=`, `Group=`, paths, and writable directories. Details are in [Testing and Operations](docs/testing-and-operations.md).
