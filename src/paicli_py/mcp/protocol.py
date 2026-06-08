from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class McpToolDescriptor:
    server_name: str
    name: str
    description: str
    input_schema: dict[str, Any]

    @property
    def namespaced_name(self) -> str:
        return f"mcp__{self.server_name}__{self.name}"


@dataclass(frozen=True)
class McpContent:
    type: str
    text: str = ""
    data: str = ""
    mime_type: str = ""


def sanitize_schema(schema: dict[str, Any] | None, max_description: int = 400) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    return _sanitize_node(schema, max_description)


def _sanitize_node(value: Any, max_description: int) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if key in {"$ref", "$defs", "definitions"}:
                continue
            if key in {"anyOf", "oneOf", "allOf"}:
                merged = _merge_schema_list(child, max_description)
                if merged:
                    result.update(merged)
                continue
            if key == "description" and isinstance(child, str):
                result[key] = child[:max_description]
                continue
            result[key] = _sanitize_node(child, max_description)
        return result
    if isinstance(value, list):
        return [_sanitize_node(item, max_description) for item in value[:20]]
    return value


def _merge_schema_list(value: Any, max_description: int) -> dict[str, Any]:
    if not isinstance(value, list) or not value:
        return {}
    first_object = next((item for item in value if isinstance(item, dict) and item.get("type") == "object"), None)
    fallback = next((item for item in value if isinstance(item, dict)), None)
    selected = first_object or fallback
    return _sanitize_node(selected, max_description) if selected else {}

