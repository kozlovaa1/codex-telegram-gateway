# Project: Codex Telegram Gateway

## Overview

Codex Telegram Gateway is a Python 3.12+ service that exposes Codex CLI through a Telegram bot. Each Telegram chat or topic is routed to a workspace binding with its own persisted Codex session, so conversational context stays isolated across chats, forum topics, and threaded private conversations.

The project is intentionally stdlib-first and deploys as a small long-polling service. It focuses on safe workspace routing, controlled subprocess execution of `codex exec` and `codex exec resume`, and operational simplicity under `systemd`.

## Core Features

- Telegram long polling bot with slash-command based control flow
- Per `chat_id + thread_id` workspace bindings with SQLite persistence
- Per-workspace Codex session tracking and resumable runs
- Separate subprocess execution for every Codex request
- Path allowlist enforcement for workspace selection and binding
- Topic-aware behavior for forum supergroups and threaded private chats
- Runtime controls for sandbox mode, approvals policy, model selection, and session reset

## Tech Stack

- **Language:** Python 3.12+
- **Framework:** Standard library only
- **Database:** SQLite
- **ORM:** None, direct `sqlite3`
- **Integrations:** Telegram Bot API, Codex CLI, systemd-based Linux deployment

## Architecture Notes

The codebase is organized as a small layered service with clear module boundaries:

- transport and command orchestration in `app.py`
- Codex subprocess lifecycle in `codex_adapter.py`
- state persistence in `workspace_store.py`
- per-workspace concurrency and queue control in `session_manager.py`
- path validation and external API wrappers in dedicated modules

This is best served by a layered architecture with explicit boundaries between Telegram-facing orchestration, session/runtime coordination, persistence, and infrastructure adapters.

## Non-Functional Requirements

- Logging: structured JSON logs with file rotation
- Error handling: fail-safe user messages with exception logging around external boundaries
- Security: workspace paths must resolve under configured allowed roots
- Isolation: session state must never leak across chats or topics
- Deployability: service should run under `systemd` with minimal dependencies
- Operability: runtime and session status must be inspectable through bot commands and logs

## Architecture

See `.ai-factory/ARCHITECTURE.md` for detailed architecture guidelines.
Pattern: Layered Architecture
