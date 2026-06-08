from __future__ import annotations

import json
from pathlib import Path


class SkillStateStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else Path.home() / ".paicli-py" / "skills.json"

    def disabled(self) -> set[str]:
        if not self.path.exists():
            return set()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        values = raw.get("disabled", []) if isinstance(raw, dict) else []
        return {str(value) for value in values}

    def set_enabled(self, name: str, enabled: bool) -> None:
        disabled = self.disabled()
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"disabled": sorted(disabled)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

