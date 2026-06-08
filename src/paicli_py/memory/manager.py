from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(frozen=True)
class MemoryEntry:
    role: str
    content: str
    created_at: float = field(default_factory=time)


class MemoryManager:
    def __init__(self, max_entries: int = 100):
        self.max_entries = max_entries
        self.short_term: list[MemoryEntry] = []
        self.long_term: list[str] = []

    def add_user_message(self, content: str) -> None:
        self._append(MemoryEntry("user", content))

    def add_assistant_message(self, content: str) -> None:
        self._append(MemoryEntry("assistant", content))

    def add_tool_result(self, tool_name: str, result: str) -> None:
        compact = result if len(result) <= 500 else result[:500] + "...(truncated)"
        self._append(MemoryEntry("tool", f"[{tool_name}] {compact}"))

    def store_fact(self, fact: str) -> None:
        fact = fact.strip()
        if fact:
            self.long_term.append(fact)

    def context_for(self, query: str, limit: int = 5) -> str:
        query_terms = {term.lower() for term in query.split() if term.strip()}
        scored = []
        for fact in self.long_term:
            fact_terms = {term.lower() for term in fact.split()}
            scored.append((len(query_terms & fact_terms), fact))
        selected = [fact for score, fact in sorted(scored, reverse=True) if score > 0][:limit]
        if not selected:
            selected = self.long_term[-limit:]
        return "\n".join(f"- {fact}" for fact in selected)

    def clear_short_term(self) -> None:
        self.short_term.clear()

    def status(self) -> str:
        return f"short_term={len(self.short_term)} entries, long_term={len(self.long_term)} facts"

    def _append(self, entry: MemoryEntry) -> None:
        self.short_term.append(entry)
        if len(self.short_term) > self.max_entries:
            del self.short_term[: len(self.short_term) - self.max_entries]

