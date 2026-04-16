from __future__ import annotations

from pathlib import Path


class PathSecurityError(ValueError):
    pass


def resolve_workspace_path(raw_path: str, allowed_roots: list[Path]) -> Path:
    candidate = Path(raw_path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PathSecurityError(f"Path does not exist: {raw_path}") from exc
    if not resolved.is_dir():
        raise PathSecurityError(f"Workspace path is not a directory: {resolved}")
    for root in allowed_roots:
        resolved_root = root.resolve(strict=True)
        try:
            resolved.relative_to(resolved_root)
            return resolved
        except ValueError:
            continue
    raise PathSecurityError(f"Path is outside allowed roots: {resolved}")


def alias_for_project(path: Path, project_roots: list[Path]) -> str | None:
    for root in project_roots:
        try:
            relative = path.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (FileNotFoundError, ValueError):
            continue
        if len(relative.parts) == 1:
            return f"project:{relative.parts[0]}"
    return None
