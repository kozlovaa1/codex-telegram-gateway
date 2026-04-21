[← Previous Page](cli.md) · [Back to README](../README.md) · [Next Page →](testing-and-operations.md)

# Architecture

The gateway uses a small layered design with explicit boundaries between Telegram orchestration, runtime coordination, persistence, and infrastructure adapters.

## Main Modules

| Module | Responsibility |
| --- | --- |
| `app.py` | Telegram update handling, command routing, workspace resolution |
| `response_ux.py` | Request-scoped Telegram response lifecycle and fallback behavior |
| `session_manager.py` | Per-workspace locking, queueing, runtime tracking, and lifecycle |
| `execution_policy.py` | Profile resolution and authorization checks |
| `workspace_store.py` | SQLite persistence for workspaces, bindings, policies, and session state |
| `workspace_preflight.py` | Filesystem readiness and canonical-path diagnostics |
| `path_security.py` | Allowed-root path validation |
| `codex_adapter.py` | `codex exec` launch, streaming, and final-message capture |
| `telegram_api.py` | Telegram Bot API wrapper |
| `config.py` | TOML and environment loading with validation |

## Runtime Flow

1. `__main__.py` loads config and wires dependencies.
2. `GatewayApp` starts Telegram long polling.
3. A message resolves to a `ChatScope`.
4. The workspace binding is loaded from `WorkspaceStore`.
5. `SessionManager` runs preflight and resolves effective execution policy.
6. `CodexAdapter` starts `codex exec` or `codex exec resume`.
7. `ResponseUxCoordinator` translates events into Telegram delivery behavior.
8. Final session and policy state are written back to SQLite.

## Persistence Model

SQLite stores four main concepts:

| Table | Purpose |
| --- | --- |
| `workspaces` | Named workspace aliases and paths |
| `bindings` | `chat_id + thread_id` to workspace mapping |
| `execution_policies` | Stored profile and override state |
| `session_states` | Session id, model, busy state, and lifecycle timestamps |

Important invariants:

- bindings are keyed by Telegram scope
- policy and session state are keyed by workspace name
- session ids normalize empty strings to `NULL`
- topic-specific internal workspaces remain isolated from base aliases

## Execution Model

The gateway runs Codex non-interactively:

- one subprocess per request
- session resume through stored `session_id`
- gateway-side enforcement for approval policy, network mode, and command rules
- sandbox passed through compatible Codex CLI flags only
- final assistant output read from `--output-last-message`

## Isolation and Safety

- Workspace paths are canonicalized and checked against `allowed_roots`.
- Preflight rejects unreadable or unwritable workspaces before execution starts.
- Each workspace has its own lock and queue cap.
- Session state must never be shared across unrelated chats or topics.

## Startup Wiring

`__main__.py` builds the service in this order:

1. config
2. logging
3. policy resolver
4. preflight checker
5. workspace store
6. Telegram API
7. response UX coordinator
8. Codex adapter
9. session manager
10. gateway app

This keeps dependency direction shallow and avoids Telegram concerns leaking into persistence or adapter modules.

## See Also

- [Configuration](configuration.md)
- [CLI and Telegram Commands](cli.md)
- [Testing and Operations](testing-and-operations.md)
