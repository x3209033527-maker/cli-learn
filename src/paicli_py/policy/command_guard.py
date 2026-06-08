from __future__ import annotations

import re

from .policy_error import PolicyError


class CommandGuard:
    BLOCK_PATTERNS = [
        r"\brm\s+-rf\s+[/\\]",
        r"\bdel\s+/[fsq]\s+[a-zA-Z]:\\",
        r"\bformat\s+[a-zA-Z]:",
        r"\bmkfs\b",
        r"\bdd\s+.*\bof=/dev/",
        r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;",
        r"\bcurl\b.*\|\s*(sh|bash|powershell)",
        r"\bwget\b.*\|\s*(sh|bash|powershell)",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bchmod\s+777\s+[/\\]",
        r"\bfind\s+[/\\]\s+",
    ]

    def validate(self, command: str) -> None:
        lowered = command.lower()
        for pattern in self.BLOCK_PATTERNS:
            if re.search(pattern, lowered, flags=re.IGNORECASE):
                raise PolicyError(f"blocked dangerous command: {command}")

