from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class McpEvent:
    level: str
    event: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def format(self) -> str:
        suffix = ""
        if self.details:
            detail_text = " ".join(f"{key}={value}" for key, value in sorted(self.details.items()))
            suffix = f" ({detail_text})"
        return f"[{self.level}] {self.event}: {self.message}{suffix}"
