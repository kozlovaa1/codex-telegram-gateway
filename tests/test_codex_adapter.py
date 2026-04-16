from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from codex_telegram_gateway.codex_adapter import CodexAdapter


class CodexAdapterTests(unittest.TestCase):
    def test_prepare_runtime_home_copies_auth_when_api_key_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            source_home = Path(tmp) / "source-codex"
            source_home.mkdir(parents=True)
            auth_path = source_home / "auth.json"
            auth_path.write_text('{"token":"chatgpt-login"}', encoding="utf-8")

            previous = os.environ.pop("OPENAI_API_KEY", None)
            try:
                adapter = CodexAdapter(
                    codex_bin="/bin/true",
                    runtime_dir=runtime_dir,
                    timeout_seconds=10,
                    kill_grace_seconds=1,
                    auth_source_home=source_home,
                )
                adapter.prepare_runtime_home()
                copied = runtime_dir / "home" / ".codex" / "auth.json"
                self.assertTrue(copied.exists())
                self.assertEqual(copied.read_text(encoding="utf-8"), '{"token":"chatgpt-login"}')
            finally:
                if previous is not None:
                    os.environ["OPENAI_API_KEY"] = previous

    def test_prepare_runtime_home_skips_auth_copy_when_api_key_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            source_home = Path(tmp) / "source-codex"
            source_home.mkdir(parents=True)
            (source_home / "auth.json").write_text('{"token":"chatgpt-login"}', encoding="utf-8")

            previous = os.environ.get("OPENAI_API_KEY")
            os.environ["OPENAI_API_KEY"] = "sk-test"
            try:
                adapter = CodexAdapter(
                    codex_bin="/bin/true",
                    runtime_dir=runtime_dir,
                    timeout_seconds=10,
                    kill_grace_seconds=1,
                    auth_source_home=source_home,
                )
                adapter.prepare_runtime_home()
                copied = runtime_dir / "home" / ".codex" / "auth.json"
                self.assertFalse(copied.exists())
            finally:
                if previous is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
