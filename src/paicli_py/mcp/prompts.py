from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class McpPromptArgument:
    name: str
    description: str = ""
    required: bool = False


@dataclass(frozen=True)
class McpPromptDescriptor:
    server_name: str
    name: str
    description: str = ""
    arguments: list[McpPromptArgument] = field(default_factory=list)


@dataclass(frozen=True)
class McpPromptMessage:
    role: str
    content_type: str
    text: str = ""
    data: str = ""
    mime_type: str = ""


def parse_prompt_arguments(raw: Any) -> list[McpPromptArgument]:
    if not isinstance(raw, list):
        return []
    parsed = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        if not name:
            continue
        parsed.append(McpPromptArgument(
            name=name,
            description=str(item.get("description", "")),
            required=bool(item.get("required", False)),
        ))
    return parsed
