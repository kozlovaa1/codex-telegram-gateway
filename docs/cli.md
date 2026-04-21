[← Previous Page](configuration.md) · [Back to README](../README.md) · [Next Page →](architecture.md)

# CLI and Telegram Commands

The gateway is driven through Telegram commands. Normal user prompts that do not start with `/` are forwarded to Codex in the currently resolved workspace.

## Command Reference

| Command | Purpose |
| --- | --- |
| `/start` | Show readiness message and current workspace |
| `/help` | Show the command list |
| `/status` | Show current workspace, session, policy, and runtime status |
| `/where` | Show the workspace binding for the current scope |
| `/pwd` | Alias for `/where` |
| `/workspaces` | List configured and dynamic workspace aliases |
| `/bind <name> <path>` | Create or update an alias and bind it to the current scope |
| `/use <name>` | Use an existing alias in the current scope or create a topic-scoped session |
| `/session show` | Alias for `/status` |
| `/session profile <name>` | Change the execution profile for the current workspace |
| `/session restart` | Force a fresh session for the next run |
| `/session reset` | Stop active work, clear session state, and use a fresh session |
| `/newsession` | Alias for reset behavior |
| `/resetsession` | Alias for reset behavior |
| `/stop` | Stop the active Codex run in this workspace |
| `/execmode [readonly|workspace-write]` | Legacy sandbox override |
| `/approvals [never|untrusted]` | Legacy approvals override |
| `/model [name]` | Show or set the workspace model |
| `/debugstatus` | Show internal runtime diagnostics |

## Authorization

These commands may be restricted by `TELEGRAM_ADMIN_IDS`, `[admin_only]`, or both:

- `/bind`
- `/use`
- `/execmode`
- `/approvals`
- privileged `/session profile ...` transitions
- `/debugstatus`

The gateway delegates these checks to `execution_policy.py` so authorization logic stays centralized.

## Workspace Resolution

For every prompt or command, the gateway resolves the current workspace in this order:

1. Explicit binding for the current `chat_id + thread_id`
2. `default_workspace_name`, if configured

`/workspaces` combines:

- entries from `[workspace_defaults]`
- dynamic `project:<name>` aliases discovered under `project_alias_roots`

## Topic and Thread Behavior

`/use <name>` behaves differently by chat type:

- in a normal chat, the current scope is rebound directly
- in a forum supergroup, a new topic is created
- in a threaded private chat, a new thread-scoped session workspace is created

Internal topic sessions use names like `session:<chat_id>:<thread_id>:<base_name>`. These are persisted but intentionally hidden from user-facing workspace lists.

## Session Lifecycle

Key behaviors:

- each workspace keeps its own persisted `session_id`
- each prompt launches a separate `codex` subprocess
- active work in the same workspace is serialized
- profile changes and legacy policy overrides clear the stored `session_id`
- `/stop` terminates the current subprocess

## Response Delivery

Private chats and group chats can use different UX policies. Depending on config, Telegram may receive:

- reactions
- typing heartbeats
- aggregated progress updates
- streamed text edits
- final-only responses

If Telegram transport features fail for a specific chat, the gateway downgrades capability and falls back to safer delivery paths.

## Typical Operator Flow

```text
/workspaces
/use project:myproj
/status
/session profile ops
/model gpt-5.4
```

## See Also

- [Configuration](configuration.md)
- [Architecture](architecture.md)
- [Testing and Operations](testing-and-operations.md)
