from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from codex_telegram_gateway.codex_adapter import CodexAdapter
from codex_telegram_gateway.models import CodexRunResult
from codex_telegram_gateway.session_manager import SessionManager
from codex_telegram_gateway.workspace_store import WorkspaceStore


class FakeProcess:
    def __init__(self) -> None:
        self.returncode = 0


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, *, workspace_path: str, prompt: str, session_id: str | None, model: str | None, sandbox_mode: str, approval_policy: str, on_event=None, on_process=None):
        self.calls.append(prompt)
        if on_process:
            on_process(FakeProcess())
        if on_event:
            await on_event({"type": "message.delta", "text": f"reply:{prompt}"})
        return (
            CodexRunResult(
                ok=True,
                final_text=f"reply:{prompt}",
                session_id="session-1",
                exit_code=0,
                duration_seconds=0.1,
                errors=[],
                raw_events=[],
            ),
            FakeProcess(),
        )

    async def _terminate(self, proc) -> None:
        return None


class SessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_updates_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp) / "db.sqlite3", {"demo": "/srv/projects/demo"}, None, "workspace-write", "never")
            store.initialize()
            adapter = FakeAdapter()
            manager = SessionManager(store, adapter, __import__("logging").getLogger("test"), 1.0, 3600, 4, 1, 4)
            seen: list[str] = []

            async def on_event(event):
                seen.append(event["text"])

            result = await manager.execute("demo", "/srv/projects/demo", "hello", on_event)
            self.assertTrue(result.ok)
            self.assertEqual(seen, ["reply:hello"])
            session = store.get_session("demo")
            self.assertEqual(session.session_id, "session-1")


if __name__ == "__main__":
    unittest.main()
