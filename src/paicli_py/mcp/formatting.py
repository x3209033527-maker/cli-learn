from __future__ import annotations

from .protocol import McpContent


def format_mcp_content(contents: list[McpContent]) -> str:
    if not contents:
        return ""
    lines = []
    for item in contents:
        if item.type == "text":
            lines.append(item.text)
        elif item.type == "image":
            lines.append(f"[image {item.mime_type or 'application/octet-stream'} {len(item.data)} chars]")
        else:
            lines.append(item.text or item.data or f"[{item.type}]")
    return "\n".join(line for line in lines if line)

