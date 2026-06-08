from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ImageContent:
    mime_type: str
    data: str
    detail: str = "auto"

    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.data}"


@dataclass
class Message:
    role: Role
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    images: list[ImageContent] = field(default_factory=list)

    @staticmethod
    def system(content: str) -> "Message":
        return Message("system", content)

    @staticmethod
    def user(content: str, images: list[ImageContent] | None = None) -> "Message":
        return Message("user", content, images=images or [])

    @staticmethod
    def assistant(content: str, tool_calls: list[ToolCall] | None = None) -> "Message":
        return Message("assistant", content, tool_calls=tool_calls or [])

    @staticmethod
    def tool(tool_call_id: str, content: str) -> "Message":
        return Message("tool", content, tool_call_id=tool_call_id)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ChatResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)
