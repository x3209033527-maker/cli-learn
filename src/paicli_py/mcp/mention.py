from __future__ import annotations

import re
from dataclasses import dataclass

from .manager import McpServerManager
from .resources import McpResourceContent


MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_.-]+):([^\s]+)")


@dataclass(frozen=True)
class McpMention:
    server_name: str
    uri: str
    raw: str


def parse_mentions(text: str) -> list[McpMention]:
    return [
        McpMention(match.group(1), match.group(2), match.group(0))
        for match in MENTION_PATTERN.finditer(text)
        if match.group(1).lower() != "image"
    ]


class AtMentionExpander:
    def __init__(self, manager: McpServerManager):
        self.manager = manager

    def expand(self, text: str) -> str:
        mentions = parse_mentions(text)
        if not mentions:
            return text
        expanded_blocks = []
        for mention in mentions:
            try:
                contents = self.manager.read_resource(mention.server_name, mention.uri)
                expanded_blocks.append(_format_resource_block(mention.server_name, mention.uri, contents))
            except Exception as exc:
                expanded_blocks.append(
                    f"<resource server=\"{mention.server_name}\" uri=\"{mention.uri}\" error=\"{exc}\" />"
                )
        return "\n".join(expanded_blocks) + "\n\n" + text


def _format_resource_block(server_name: str, uri: str, contents: list[McpResourceContent]) -> str:
    if not contents:
        return f"<resource server=\"{server_name}\" uri=\"{uri}\"></resource>"
    parts = []
    for content in contents:
        body = content.text or (f"[blob {content.mime_type} {len(content.blob)} chars]" if content.blob else "")
        parts.append(body)
    return (
        f"<resource server=\"{server_name}\" uri=\"{uri}\">\n"
        + "\n".join(parts)
        + "\n</resource>"
    )
