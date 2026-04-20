from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .path_security import PathSecurityError, resolve_workspace_path


LOGGER = logging.getLogger("codex_telegram_gateway.workspace_preflight")


@dataclass(frozen=True, slots=True)
class PreflightDiagnostic:
    check_name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class WorkspacePreflightResult:
    workspace_name: str
    requested_path: str
    canonical_path: str | None
    codex_dir: str | None
    diagnostics: tuple[PreflightDiagnostic, ...]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.diagnostics)

    @property
    def user_message(self) -> str:
        for item in self.diagnostics:
            if not item.ok:
                return f"Workspace preflight failed ({item.check_name}): {item.detail}"
        return "Workspace preflight passed."


class WorkspacePreflightError(RuntimeError):
    def __init__(self, result: WorkspacePreflightResult) -> None:
        super().__init__(result.user_message)
        self.result = result


class WorkspacePreflightChecker:
    def __init__(self, allowed_roots: list[Path]) -> None:
        self.allowed_roots = allowed_roots

    def run(self, workspace_name: str, workspace_path: str) -> WorkspacePreflightResult:
        LOGGER.debug(
            "workspace_preflight_started",
            extra={"extra_fields": {"workspace_name": workspace_name, "workspace_path": workspace_path}},
        )
        diagnostics: list[PreflightDiagnostic] = []
        try:
            resolved = resolve_workspace_path(workspace_path, self.allowed_roots)
        except PathSecurityError as exc:
            result = WorkspacePreflightResult(
                workspace_name=workspace_name,
                requested_path=workspace_path,
                canonical_path=None,
                codex_dir=None,
                diagnostics=(PreflightDiagnostic("path_security", False, str(exc)),),
            )
            self._log_failure(result)
            return result
        except OSError:
            LOGGER.error(
                "workspace_preflight_exception",
                extra={"extra_fields": {"workspace_name": workspace_name, "workspace_path": workspace_path, "check_name": "path_security"}},
                exc_info=True,
            )
            result = WorkspacePreflightResult(
                workspace_name=workspace_name,
                requested_path=workspace_path,
                canonical_path=None,
                codex_dir=None,
                diagnostics=(PreflightDiagnostic("path_security", False, "Unexpected filesystem error."),),
            )
            self._log_failure(result)
            return result

        diagnostics.append(PreflightDiagnostic("path_security", True, "Workspace path is within allowed roots."))
        diagnostics.append(self._access_check(workspace_name, resolved, "read_access", "Workspace is not readable.", "readable"))
        diagnostics.append(self._access_check(workspace_name, resolved, "write_access", "Workspace is not writable.", "writable"))
        codex_dir = resolved / ".codex"
        try:
            codex_dir.mkdir(parents=True, exist_ok=True)
            diagnostics.append(PreflightDiagnostic("codex_dir", True, ".codex is ready."))
        except OSError:
            LOGGER.error(
                "workspace_preflight_exception",
                extra={
                    "extra_fields": {
                        "workspace_name": workspace_name,
                        "workspace_path": workspace_path,
                        "canonical_path": str(resolved),
                        "check_name": "codex_dir",
                    }
                },
                exc_info=True,
            )
            result = WorkspacePreflightResult(
                workspace_name=workspace_name,
                requested_path=workspace_path,
                canonical_path=str(resolved),
                codex_dir=str(codex_dir),
                diagnostics=tuple(diagnostics + [PreflightDiagnostic("codex_dir", False, "Failed to prepare .codex directory.")]),
            )
            self._log_failure(result)
            return result

        probe_result = self._probe_write_delete(workspace_name, resolved, codex_dir)
        diagnostics.append(probe_result)
        result = WorkspacePreflightResult(
            workspace_name=workspace_name,
            requested_path=workspace_path,
            canonical_path=str(resolved),
            codex_dir=str(codex_dir),
            diagnostics=tuple(diagnostics),
        )
        if result.ok:
            LOGGER.info(
                "workspace_preflight_succeeded",
                extra={
                    "extra_fields": {
                        "workspace_name": workspace_name,
                        "workspace_path": workspace_path,
                        "canonical_path": str(resolved),
                    }
                },
            )
        else:
            self._log_failure(result)
        return result

    def _access_check(
        self,
        workspace_name: str,
        resolved: Path,
        check_name: str,
        failure_detail: str,
        mode_name: str,
    ) -> PreflightDiagnostic:
        try:
            allowed = resolved.stat() is not None and resolved.exists()
        except OSError:
            LOGGER.error(
                "workspace_preflight_exception",
                extra={
                    "extra_fields": {
                        "workspace_name": workspace_name,
                        "canonical_path": str(resolved),
                        "check_name": check_name,
                    }
                },
                exc_info=True,
            )
            return PreflightDiagnostic(check_name, False, "Unexpected filesystem error.")
        if mode_name == "readable":
            try:
                next(iter(resolved.iterdir()), None)
            except PermissionError:
                return PreflightDiagnostic(check_name, False, failure_detail)
            except OSError:
                LOGGER.error(
                    "workspace_preflight_exception",
                    extra={
                        "extra_fields": {
                            "workspace_name": workspace_name,
                            "canonical_path": str(resolved),
                            "check_name": check_name,
                        }
                    },
                    exc_info=True,
                )
                return PreflightDiagnostic(check_name, False, "Unexpected filesystem error.")
        if mode_name == "writable" and not os.access(resolved, os.W_OK):
            return PreflightDiagnostic(check_name, False, failure_detail)
        if not allowed:
            return PreflightDiagnostic(check_name, False, failure_detail)
        return PreflightDiagnostic(check_name, True, f"Workspace is {mode_name}.")

    def _probe_write_delete(self, workspace_name: str, resolved: Path, codex_dir: Path) -> PreflightDiagnostic:
        probe_path = codex_dir / f".gateway-preflight-{uuid4().hex}"
        try:
            probe_path.write_text("ok", encoding="utf-8")
            probe_path.unlink()
            return PreflightDiagnostic("write_probe", True, "Write/delete probe passed.")
        except OSError:
            LOGGER.error(
                "workspace_preflight_exception",
                extra={
                    "extra_fields": {
                        "workspace_name": workspace_name,
                        "canonical_path": str(resolved),
                        "check_name": "write_probe",
                    }
                },
                exc_info=True,
            )
            return PreflightDiagnostic("write_probe", False, "Create/delete probe failed.")

    def _log_failure(self, result: WorkspacePreflightResult) -> None:
        for item in result.diagnostics:
            if item.ok:
                continue
            LOGGER.warning(
                "preflight_failed",
                extra={
                    "extra_fields": {
                        "workspace_name": result.workspace_name,
                        "workspace_path": result.requested_path,
                        "canonical_path": result.canonical_path,
                        "check_name": item.check_name,
                        "detail": item.detail,
                    }
                },
            )
