# Project Base Rules

> Auto-detected conventions from codebase analysis. Edit as needed.

## Naming Conventions

- Files: `snake_case.py` modules under `src/codex_telegram_gateway/`
- Variables: `snake_case`
- Functions: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`

## Module Structure

- Keep application orchestration in `src/codex_telegram_gateway/app.py`
- Keep external process integration in adapter modules such as `codex_adapter.py`
- Keep persistence logic in `workspace_store.py`
- Keep cross-request runtime coordination in `session_manager.py`
- Keep API boundary wrappers isolated in modules like `telegram_api.py`

## Error Handling

- Raise explicit domain or boundary exceptions for invalid state at module boundaries
- Convert external failures into user-safe responses in the Telegram-facing layer
- Log operational failures with structured context instead of swallowing exceptions

## Logging

- Use the structured logging helpers in `logging_utils.py`
- Emit JSON log records with contextual fields for bot updates and Codex runs
- Keep secrets and raw credentials out of logs

## Testing

- Use `unittest` with focused module-level test files in `tests/`
- Prefer deterministic fakes over live integrations for session and adapter behavior
- Keep tests runnable with `PYTHONPATH=src python3 -m unittest discover -s tests -v`
