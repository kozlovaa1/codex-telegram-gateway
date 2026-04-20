# AGENTS.md

> Project map for AI agents. Keep this file up-to-date as the project evolves.

## Project Overview

`codex-telegram-gateway` is a Telegram wrapper around `Codex CLI`. It binds each Telegram chat or topic to a workspace and preserves one Codex session per workspace so context does not leak across chats, threads, or topics.

## Tech Stack

- **Language:** Python 3.12+
- **Framework:** Standard library only
- **Database:** SQLite
- **ORM:** None, direct `sqlite3`

## Project Structure

```text
.
├── src/codex_telegram_gateway/      # Application package
│   ├── __main__.py                  # CLI entrypoint
│   ├── app.py                       # Telegram update handling and slash commands
│   ├── codex_adapter.py             # Codex subprocess execution and streaming
│   ├── config.py                    # TOML and environment config loading
│   ├── execution_policy.py          # Workspace execution policy resolution and authorization
│   ├── logging_utils.py             # Structured JSON logging helpers
│   ├── models.py                    # Shared data records
│   ├── path_security.py             # Canonical path allowlist checks
│   ├── rate_limit.py                # Per-user rate limiting
│   ├── response_ux.py               # Telegram response lifecycle orchestration
│   ├── session_manager.py           # Per-workspace serialization and queueing
│   ├── telegram_api.py              # Telegram Bot API wrapper
│   ├── workspace_preflight.py       # Workspace filesystem preflight diagnostics
│   └── workspace_store.py           # SQLite persistence for workspaces and sessions
├── tests/                           # Unit tests for core gateway behavior
├── systemd/                         # Example service unit
├── .ai-factory/                     # AI Factory project context artifacts
├── .codex/config.toml               # Project-local Codex MCP configuration
├── README.md                        # Project overview and deployment guide
├── config.toml                      # Runtime configuration
├── config.example.toml              # Config template
└── pyproject.toml                   # Package metadata and entrypoint
```

## Key Entry Points

| File | Purpose |
|------|---------|
| `src/codex_telegram_gateway/__main__.py` | Starts the gateway process |
| `src/codex_telegram_gateway/app.py` | Main Telegram command and routing logic |
| `src/codex_telegram_gateway/codex_adapter.py` | Launches `codex exec` and `codex exec resume` |
| `src/codex_telegram_gateway/execution_policy.py` | Resolves effective execution profile and centralized admin checks |
| `src/codex_telegram_gateway/response_ux.py` | Owns Telegram response lifecycle and prompt delivery UX orchestration |
| `src/codex_telegram_gateway/workspace_preflight.py` | Validates workspace readiness before session start/restart |
| `src/codex_telegram_gateway/workspace_store.py` | Persists workspace bindings and sessions |
| `config.toml` | Runtime paths, Codex options, and workspace defaults |
| `.codex/config.toml` | Project-local Codex MCP servers such as Context7 |
| `.env` | Secrets such as Telegram token and optional API key |

## Documentation

| Document | Path | Description |
|----------|------|-------------|
| README | `README.md` | Project landing page and deployment notes |
| Systemd unit | `systemd/codex-telegram-gateway.service` | Example service definition |

## AI Context Files

| File | Purpose |
|------|---------|
| `AGENTS.md` | This file, the project structure map |
| `.ai-factory/DESCRIPTION.md` | Project specification and stack summary |
| `.ai-factory/ARCHITECTURE.md` | Architecture decisions and dependency rules |
| `.ai-factory/config.yaml` | AI Factory language, path, and git workflow settings |
| `.ai-factory/rules/base.md` | Auto-detected coding conventions |

## Core Behavior

- one `chat_id + thread_id` maps to one workspace binding
- each workspace keeps its own Codex session id
- each workspace keeps its own execution policy and busy/idle session state
- requests are executed through separate `codex exec` or `codex exec resume` subprocesses
- session context must not be mixed across chats or topics
- long polling is used instead of webhooks

## Main Components

- `src/codex_telegram_gateway/app.py`
  - Telegram update handling
  - slash commands
  - workspace routing
  - topic and thread behavior
- `src/codex_telegram_gateway/codex_adapter.py`
  - launches `codex exec`
  - streams JSON events
  - reads final assistant output from `--output-last-message`
- `src/codex_telegram_gateway/execution_policy.py`
  - resolves effective profile for each workspace
  - applies precedence between defaults, durable overrides, and break-glass
  - centralizes admin-only authorization for privileged transitions
- `src/codex_telegram_gateway/workspace_store.py`
  - SQLite storage for workspaces, bindings, execution policies, and session state
- `src/codex_telegram_gateway/session_manager.py`
  - per-workspace serialization
  - queue limiting
  - start, stop, restart, and policy-change behavior
- `src/codex_telegram_gateway/path_security.py`
  - canonical path validation against `allowed_roots`
- `src/codex_telegram_gateway/response_ux.py`
  - request-scoped Telegram response lifecycle orchestration
  - duplicate responder protection and prompt delivery cleanup
- `src/codex_telegram_gateway/workspace_preflight.py`
  - canonical-path and filesystem readiness checks before execution
- `src/codex_telegram_gateway/telegram_api.py`
  - Telegram Bot API wrapper

## Config and Secrets

Runtime configuration:

- `.env`
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_ADMIN_IDS`
  - optional `OPENAI_API_KEY`
  - optional `CONTEXT7_API_KEY`
- local `npx` availability is required if `.codex/config.toml` enables `context7` via `@upstash/context7-mcp`
- `config.toml`
  - paths
  - allowed roots
  - workspace defaults
  - execution profiles
  - admin-only command switches
  - break-glass TTL
  - default workspace
  - Codex runtime settings
- `.codex/config.toml`
  - project-local MCP server configuration for Codex
  - includes `context7` for up-to-date documentation lookup when `CONTEXT7_API_KEY` is present

Auth behavior:

- if `OPENAI_API_KEY` is set, Codex uses it
- otherwise the gateway copies `auth.json` from `codex_auth_source_home` into its isolated runtime home
- service user needs read access to `codex_auth_source_home/auth.json` if API key is not used

## Workspace Model

- explicit binding: `/bind <name> <path>` or `/use <name>`
- default binding: `default_workspace_name` for unbound chats and topics
- project aliases: top-level directories under `project_alias_roots` become `project:<name>`

Internal session workspaces:

- threaded chats and topics may create internal workspace names like `session:<chat_id>:<thread_id>:<base_name>`
- these should not be exposed as normal user-facing workspace aliases

## Topics and Threads

Supported behavior:

- forum supergroups: `/use <workspace>` creates a new topic with a separate Codex session
- personal chats with Telegram `Threaded mode`: `/use <workspace>` also creates a new topic or thread with a separate Codex session
- normal chats without topics: `/use <workspace>` binds the current chat directly

Operational note:

- for personal chat topics, `Threaded mode` must be enabled for the bot in `@BotFather`

## Execution Model

Gateway uses `codex exec` in non-interactive mode.

Important details:

- do not pass `--ask-for-approval` to `codex exec`
- sandbox is passed to Codex CLI only through confirmed compatible flags
- approvals, network mode, and command rules are enforced gateway-side first
- final assistant message is captured through `--output-last-message`
- JSON output is used for progress and status streaming, not as the only source of the final answer
- sandbox mode is passed through `--sandbox`

## Tests

Run:

```bash
cd <project_dir>
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Current coverage focuses on:

- path validation
- workspace policy/session store behavior and migration
- default workspace resolution
- execution-policy resolution and admin authorization
- workspace preflight diagnostics
- session restart/profile lifecycle and legacy command compatibility
- topic and session naming helpers
- bootstrap wiring for policy and preflight services
- auth fallback behavior

## Systemd

Repository unit file is a template, not a drop-in universal truth.

Before enabling it:

- replace `User=` and `Group=`
- replace `<project_dir>`, `<state_dir>`, `<log_dir>`, `<codex_home_dir>`, `<allowed_root_*>`
- ensure service user can write `runtime_dir` and `log_dir`
- ensure service user can execute `codex_bin`

Typical apply flow after code changes:

```bash
cd <project_dir>
.venv/bin/pip install -e .
sudo systemctl restart codex-telegram-gateway
sudo systemctl status codex-telegram-gateway
```

## Known Constraints

- no webhook mode yet
- no interactive approval flow via Telegram yet
- sandbox restrictions still depend on real filesystem permissions of the selected workspace
- server-ops checks should use a workspace that is writable by the service user

## Agent Rules

- Never combine shell commands with `&&`, `||`, or `;`. Execute each command as a separate shell call.
- Keep session isolation intact. Do not introduce shortcuts that can mix state across unrelated chats or topics.
- Preserve stdlib-first deployment unless the change has a clear operational payoff.
- Treat `systemd` files and runtime paths as environment-specific templates, not universal defaults.
- For library or API documentation lookups, prefer the `context7` MCP server when it is configured and available.
