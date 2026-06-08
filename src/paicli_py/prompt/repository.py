from __future__ import annotations

from importlib import resources
from pathlib import Path


class PromptRepository:
    def __init__(self, project_dir: str | Path | None = None, user_dir: str | Path | None = None):
        self.project_dir = Path(project_dir).resolve() if project_dir else None
        self.user_dir = Path(user_dir) if user_dir else Path.home() / ".paicli-py" / "prompts"

    def read(self, relative_path: str) -> str:
        for path in self._candidate_paths(relative_path):
            if path.exists():
                return path.read_text(encoding="utf-8")
        try:
            return resources.files("paicli_py.prompt.resources").joinpath(relative_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def exists(self, relative_path: str) -> bool:
        if any(path.exists() for path in self._candidate_paths(relative_path)):
            return True
        try:
            return resources.files("paicli_py.prompt.resources").joinpath(relative_path).is_file()
        except FileNotFoundError:
            return False

    def _candidate_paths(self, relative_path: str) -> list[Path]:
        candidates = []
        if self.project_dir is not None:
            candidates.append(self.project_dir / ".paicli-py" / "prompts" / relative_path)
        candidates.append(self.user_dir / relative_path)
        return candidates
