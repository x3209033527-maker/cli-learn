from __future__ import annotations


class SkillContextBuffer:
    def __init__(self):
        self._entries: list[str] = []

    def add(self, skill_name: str, body: str) -> None:
        body = body.strip()
        if not body:
            return
        self._entries.append(f"<skill name=\"{skill_name}\">\n{body}\n</skill>")

    def drain(self) -> str:
        if not self._entries:
            return ""
        value = "\n\n".join(self._entries)
        self._entries.clear()
        return value

    def is_empty(self) -> bool:
        return not self._entries

