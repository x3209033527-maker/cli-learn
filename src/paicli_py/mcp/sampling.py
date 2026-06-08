from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


SamplingHandler = Callable[["SamplingRequest"], "SamplingResult"]


@dataclass(frozen=True)
class SamplingMessage:
    role: str
    content_type: str
    text: str = ""
    data: str = ""
    mime_type: str = ""


@dataclass(frozen=True)
class SamplingRequest:
    server_name: str
    messages: tuple[SamplingMessage, ...]
    system_prompt: str = ""
    max_tokens: int = 1024
    temperature: float | None = None
    model_preferences: dict[str, Any] | None = None


@dataclass(frozen=True)
class SamplingResult:
    role: str = "assistant"
    text: str = ""
    model: str = ""
    stop_reason: str = "endTurn"

    def to_mcp_result(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": {"type": "text", "text": self.text},
            "model": self.model,
            "stopReason": self.stop_reason,
        }


class SamplingRejected(RuntimeError):
    pass


class SamplingRequestParser:
    @staticmethod
    def parse(server_name: str, params: dict[str, Any] | None) -> SamplingRequest:
        payload = params if isinstance(params, dict) else {}
        messages = tuple(_parse_message(item) for item in payload.get("messages", []) if isinstance(item, dict))
        return SamplingRequest(
            server_name=server_name,
            messages=messages,
            system_prompt=str(payload.get("systemPrompt", "")),
            max_tokens=_bounded_int(payload.get("maxTokens", 1024), 1, 32768, 1024),
            temperature=_optional_float(payload.get("temperature")),
            model_preferences=payload.get("modelPreferences") if isinstance(payload.get("modelPreferences"), dict) else None,
        )


def default_sampling_handler(request: SamplingRequest) -> SamplingResult:
    raise SamplingRejected(
        f"MCP server {request.server_name} requested sampling, but no sampling handler is configured"
    )


def format_sampling_request(request: SamplingRequest) -> str:
    lines = [
        f"MCP sampling request from {request.server_name}",
        f"Messages: {len(request.messages)}",
        f"Max tokens: {request.max_tokens}",
    ]
    if request.system_prompt:
        lines.append("System prompt: " + _compact(request.system_prompt))
    for index, message in enumerate(request.messages, start=1):
        body = message.text or f"[{message.content_type} {message.mime_type} {len(message.data)} chars]"
        lines.append(f"{index}. {message.role}: {_compact(body)}")
    return "\n".join(lines)


def _parse_message(item: dict[str, Any]) -> SamplingMessage:
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    return SamplingMessage(
        role=str(item.get("role", "user")),
        content_type=str(content.get("type", "text")),
        text=str(content.get("text", "")),
        data=str(content.get("data", "")),
        mime_type=str(content.get("mimeType", "")),
    )


def _bounded_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compact(value: str, limit: int = 200) -> str:
    normalized = " ".join(str(value or "").split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."
