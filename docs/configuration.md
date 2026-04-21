[← Previous Page](getting-started.md) · [Back to README](../README.md) · [Next Page →](cli.md)

# Configuration

This page documents runtime configuration, environment variables, execution profiles, and response UX policy.

## Files

| File | Purpose |
| --- | --- |
| `.env` | Secrets and operator identity |
| `config.toml` | Runtime settings for paths, limits, policies, and Telegram behavior |
| `config.example.toml` | Baseline template |
| `.codex/config.toml` | Project-local Codex MCP configuration |

## `.env`

| Variable | Required | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram Bot API token |
| `TELEGRAM_ADMIN_IDS` | Yes | Comma-separated Telegram user ids allowed to run admin commands |
| `OPENAI_API_KEY` | No | Direct Codex/OpenAI auth path |
| `CONTEXT7_API_KEY` | No | Enables Context7 MCP when configured |

## Core Runtime Settings

These top-level `config.toml` fields should be reviewed first:

| Field | Purpose |
| --- | --- |
| `bot_name` | Human-readable bot label used in messages |
| `telegram_api_base` | Base URL for Telegram Bot API |
| `sqlite_path` | SQLite database file |
| `runtime_dir` | Runtime home, output files, and copied auth state |
| `log_dir` | Structured log destination |
| `codex_bin` | Absolute path to the `codex` binary visible to the service user |
| `codex_auth_source_home` | Source `.codex` directory for auth fallback |
| `default_workspace_name` | Fallback workspace for unbound chats or topics |
| `default_model` | Optional workspace model default |

## Workspace and Alias Settings

| Field | Purpose |
| --- | --- |
| `allowed_roots` | Canonical root allowlist for `/bind` and workspace checks |
| `project_alias_roots` | Directories whose first-level children become `project:<name>` aliases |
| `[workspace_defaults]` | Static alias-to-path map created during store initialization |
| `[workspace_profile_defaults]` | Workspace-specific default execution profile |

## Limits and Lifecycle

| Field | Purpose |
| --- | --- |
| `poll_timeout_seconds` | Telegram long-poll duration |
| `poll_retry_delay_seconds` | Retry delay after polling failure |
| `telegram_message_chunk` | Outbound message chunk size |
| `stream_edit_interval_seconds` | Stream edit cadence |
| `session_idle_ttl_seconds` | Runtime eviction threshold for idle workspaces |
| `command_timeout_seconds` | Per-run subprocess timeout |
| `process_kill_grace_seconds` | Grace period before hard kill |
| `max_parallel_processes` | Global subprocess concurrency cap |
| `max_queue_per_workspace` | Per-workspace queued request cap |
| `max_active_workspaces` | In-memory runtime cache cap |
| `per_user_rate_limit_window_seconds` | Telegram rate-limit window |
| `per_user_rate_limit_max_messages` | Allowed messages per window |

## Execution Profiles

Execution policy is stored per workspace and resolved centrally.

Built-in concepts:

- `default`: safe baseline
- `ops`: privileged operational profile
- `break-glass`: temporary emergency profile with TTL

Relevant fields:

| Field | Purpose |
| --- | --- |
| `default_sandbox_mode` | Base sandbox mode |
| `default_approval_policy` | Base approvals policy |
| `default_network_mode` | Base network mode |
| `break_glass_ttl_seconds` | Expiration window for break-glass elevation |
| `[command_rule_groups]` | Named rule bundles enforced gateway-side |
| `[execution_profiles.<name>]` | Profile definitions |
| `[admin_only]` | Per-command authorization hardening |

Precedence order:

1. Default profile values
2. Workspace profile defaults
3. Durable workspace override
4. Temporary break-glass overlay

## Telegram and Response UX

`[telegram]` controls accepted chat types:

- `allow_private_chats`
- `allow_group_chats`
- `allow_topics`

`[response_ux.private_chat]` and `[response_ux.group_chat]` control delivery behavior:

- `reaction`
- `typing`
- `progress`
- `stream`

Constraint:

- `stream = true` requires `progress = true`; invalid combinations are rejected during config load.

## Example Profile Snippet

```toml
[execution_profiles.default]
sandbox_mode = "workspace-write"
approval_policy = "never"
network_mode = "restricted"
command_rule_group = "default"
admin_only = false

[execution_profiles.ops]
sandbox_mode = "workspace-write"
approval_policy = "on-request"
network_mode = "restricted"
command_rule_group = "ops"
admin_only = true
```

## Validation Notes

The config loader rejects:

- unknown sandbox, approval, or network values
- invalid profile or rule-group names
- non-boolean admin-only flags
- invalid response UX combinations
- missing required environment variables

## See Also

- [Getting Started](getting-started.md)
- [CLI and Telegram Commands](cli.md)
- [Architecture](architecture.md)
