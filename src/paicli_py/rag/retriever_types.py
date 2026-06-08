from __future__ import annotations

from dataclasses import dataclass, field

from .analyzer import CodeRelation


@dataclass(frozen=True)
class SearchResult:
    file_path: str
    chunk_type: str
    name: str
    content: str
    start_line: int
    end_line: int
    score: float
    relations: list[CodeRelation] = field(default_factory=list)
