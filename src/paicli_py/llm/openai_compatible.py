from __future__ import annotations

import json
import urllib.request
from typing import Any

from .types import ChatResponse, Message, ToolCall, ToolDefinition


class OpenAICompatibleClient:
    def __init__(self, api_url: str, api_key: str, model: str, timeout: int = 120):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[Message], tools: list[ToolDefinition]) -> ChatResponse:
        payload = {
            "model": self.model,
            "messages": [self._message_to_json(message) for message in messages],
            "tools": [self._tool_to_json(tool) for tool in tools],
        }
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
        data = json.loads(raw)
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})
        tool_calls = []
        for call in message.get("tool_calls", []) or []:
            fn = call.get("function", {})
            arguments = fn.get("arguments") or "{}"
            if isinstance(arguments, str):
                try:
                    parsed_args = json.loads(arguments)
                except json.JSONDecodeError:
                    parsed_args = {}
            else:
                parsed_args = arguments
            tool_calls.append(ToolCall(call.get("id", ""), fn.get("name", ""), parsed_args))
        return ChatResponse(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
        )

    def _message_to_json(self, message: Message) -> dict[str, Any]:
        content: Any = message.content
        if message.images and message.role == "user":
            content = [{"type": "text", "text": message.content}]
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image.data_url(),
                        "detail": image.detail,
                    },
                }
                for image in message.images
            )
        result: dict[str, Any] = {"role": message.role, "content": content}
        if message.tool_call_id:
            result["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        return result

    def _tool_to_json(self, tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
