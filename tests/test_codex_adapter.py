from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from codex_telegram_gateway.codex_adapter import CodexAdapter, PolicyEnforcementError, ResolvedRunPolicy


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

    def test_build_command_uses_resume_and_supported_flags_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            adapter = CodexAdapter(
                codex_bin="/bin/codex",
                runtime_dir=runtime_dir,
                timeout_seconds=10,
                kill_grace_seconds=1,
            )

            command = adapter.build_command(
                workspace_path="/srv/projects/demo",
                prompt="hello",
                session_id="session-1",
                model="gpt-5",
                sandbox_mode="workspace-write",
                output_last_message=runtime_dir / "output" / "last.txt",
            )

            self.assertEqual(
                command,
                [
                    "/bin/codex",
                    "exec",
                    "--json",
                    "--skip-git-repo-check",
                    "--cd",
                    "/srv/projects/demo",
                    "--sandbox",
                    "workspace-write",
                    "--output-last-message",
                    str(runtime_dir / "output" / "last.txt"),
                    "--model",
                    "gpt-5",
                    "resume",
                    "session-1",
                    "hello",
                ],
            )
            self.assertNotIn("--ask-for-approval", command)

    def test_command_rule_violation_is_rejected_by_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            adapter = CodexAdapter(
                codex_bin="/bin/codex",
                runtime_dir=runtime_dir,
                timeout_seconds=10,
                kill_grace_seconds=1,
            )

            with self.assertRaises(PolicyEnforcementError):
                adapter._enforce_command_rules(
                    "please run sudo systemctl restart nginx",
                    "/srv/projects/demo",
                    ResolvedRunPolicy(
                        profile_name="default",
                        sandbox_mode="workspace-write",
                        approval_policy="never",
                        network_mode="restricted",
                        command_rule_group="default",
                        command_rules=("workspace-safe",),
                    ),
                )


if __name__ == "__main__":
    unittest.main()
