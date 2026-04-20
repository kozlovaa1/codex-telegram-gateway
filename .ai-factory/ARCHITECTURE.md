# Architecture: Layered Architecture

## Overview

This project uses a small layered architecture tailored to a long-running Telegram service with explicit integration boundaries. The gateway needs simple operational flow, clear isolation of external dependencies, and predictable handling of per-workspace state more than it needs heavy domain abstractions.

Layered architecture fits because the business rules are modest, but the boundary rules are strict: Telegram updates, filesystem path validation, SQLite persistence, and Codex subprocess execution must stay separated so session isolation and operational safety remain easy to reason about.

## Decision Rationale

- **Project type:** Telegram gateway service for Codex CLI
- **Tech stack:** Python 3.12+, standard library, SQLite
- **Key factor:** simple deployment with strong boundaries between orchestration, persistence, and external integrations

## Folder Structure

```text
src/
└── codex_telegram_gateway/
    ├── __main__.py              # CLI entrypoint
    ├── app.py                   # Telegram update handling and command orchestration
    ├── config.py                # Config loading from TOML and environment
    ├── execution_policy.py      # Execution-profile resolution and authorization
    ├── models.py                # Shared typed records and value objects
    ├── path_security.py         # Allowed-root path validation
    ├── rate_limit.py            # Per-user message rate limiting
    ├── response_ux.py           # Telegram response lifecycle orchestration
    ├── session_manager.py       # Per-workspace runtime coordination
    ├── workspace_store.py       # SQLite persistence for workspaces, bindings, sessions
    ├── workspace_preflight.py   # Workspace readiness and filesystem diagnostics
    ├── codex_adapter.py         # Codex subprocess lifecycle and event streaming
    ├── telegram_api.py          # Telegram Bot API wrapper
    └── logging_utils.py         # Structured JSON logging helpers

tests/
├── test_app_default_workspace.py
├── test_app_session_controls.py
├── test_codex_adapter.py
├── test_execution_policy.py
├── test_main.py
├── test_path_security.py
├── test_session_manager.py
├── test_workspace_preflight.py
└── test_workspace_store.py
```

## Dependency Rules

The dependency direction should stay shallow and explicit:

- `app.py` may depend on config, models, session management, persistence, path security, and API adapters
- `app.py` may depend on `response_ux.py` as the only owner of Telegram prompt delivery lifecycle state
- `app.py` may depend on execution-policy resolution for authorization checks, but policy enforcement rules should not be duplicated outside that module
- `session_manager.py` may depend on the store, shared models, logging helpers, execution-policy resolution, workspace preflight, and Codex adapter
- `response_ux.py` may depend on shared models, config, Telegram transport, and adapter event text extraction, but it should not own persistence or workspace routing
- `workspace_store.py` and `path_security.py` should remain infrastructure-oriented and independent from Telegram logic
- `execution_policy.py` should remain transport-neutral and callable from both Telegram handlers and runtime orchestration
- `telegram_api.py` and `codex_adapter.py` should not depend on application command routing

- ✅ Entry points and orchestration may call adapters and storage layers
- ✅ Shared models may be imported across layers when they remain transport-neutral
- ✅ Policy and preflight modules may be injected into orchestration layers as standalone services
- ❌ Storage and adapter modules should not import `app.py`
- ❌ Telegram-specific concerns should not leak into persistence or path validation modules

## Layer Communication

- Telegram updates enter through `app.py`, which resolves scope, binding, and command routing
- Prompt execution enters `response_ux.py` from `app.py`, which owns request-scoped delivery state and cleanup
- `app.py` delegates authorization decisions to `execution_policy.py`
- `app.py` delegates run coordination to `SessionManager`
- `SessionManager` uses `WorkspaceStore` for persisted state, `workspace_preflight.py` for readiness checks, and `CodexAdapter` for subprocess execution
- Infrastructure modules return typed values or explicit exceptions back to the orchestration layer

## Key Principles

1. Keep Telegram-facing control flow centralized in `app.py`
2. Keep subprocess execution and persistence isolated behind narrow module APIs
3. Preserve session isolation as a cross-cutting invariant in every layer

## Code Examples

### Application Layer Orchestration

```python
async def _handle_prompt(
    self,
    scope: ChatScope,
    user_id: int,
    chat_id: int,
    thread_id: int | None,
    message_id: int | None,
    text: str,
) -> None:
    resolved = self._workspace_from_scope(scope)
    if not resolved:
        await self.telegram.send_message(chat_id, "No workspace bound. Use /workspaces or /bind.", thread_id)
        return

    workspace_name, workspace_path = resolved
    result = await self.sessions.execute(workspace_name, workspace_path, user_id, text, self._stream_callback(chat_id, thread_id, message_id))
    await self.telegram.send_message(chat_id, result.final_text, thread_id)
```

### Infrastructure Boundary with Typed State

```python
def get_session(self, workspace_name: str) -> SessionRecord:
    with closing(self._connect()) as conn:
        return self.ensure_session(workspace_name, conn=conn)


async def stop_workspace(self, workspace_name: str) -> bool:
    runtime = self._runtimes.get(workspace_name)
    if not runtime or not runtime.current_process:
        return False
    await self.adapter._terminate(runtime.current_process)
    self.store.update_session(workspace_name, busy_state="idle", busy_since=None, last_stop_reason="manual_stop")
    return True
```

## Anti-Patterns

- ❌ Putting SQLite reads and writes directly inside Telegram command handlers
- ❌ Letting adapter modules make routing decisions about chats, threads, or bindings
- ❌ Sharing one Codex session across unrelated chat scopes
- ❌ Coupling path validation rules to Telegram transport details
