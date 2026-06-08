from __future__ import annotations

from pathlib import Path

from .policy_error import PolicyError


class PathGuard:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def resolve_safe(self, user_path: str | Path) -> Path:
        if str(user_path).strip() == "":
            raise PolicyError("path cannot be empty")
        raw = Path(user_path)
        target = raw if raw.is_absolute() else self.root / raw
        resolved = self._resolve_existing_parent(target)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise PolicyError(f"path escapes project root: {user_path}") from exc
        return resolved

    def _resolve_existing_parent(self, target: Path) -> Path:
        target = target.absolute()
        existing = target
        missing: list[str] = []
        while not existing.exists() and existing.parent != existing:
            missing.append(existing.name)
            existing = existing.parent
        base = existing.resolve() if existing.exists() else existing.absolute()
        for part in reversed(missing):
            base = base / part
        return base

