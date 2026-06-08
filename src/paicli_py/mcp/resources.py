from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class McpResourceDescriptor:
    server_name: str
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""


@dataclass(frozen=True)
class McpResourceContent:
    server_name: str
    uri: str
    text: str = ""
    blob: str = ""
    mime_type: str = ""


class McpResourceCache:
    def __init__(self):
        self._resources: dict[str, list[McpResourceDescriptor]] = {}

    def put(self, server_name: str, resources: list[McpResourceDescriptor]) -> None:
        self._resources[server_name] = list(resources)

    def remove_server(self, server_name: str) -> None:
        self._resources.pop(server_name, None)

    def resources(self, server_name: str | None = None) -> list[McpResourceDescriptor]:
        if server_name is not None:
            return list(self._resources.get(server_name, []))
        all_resources: list[McpResourceDescriptor] = []
        for resources in self._resources.values():
            all_resources.extend(resources)
        return all_resources

    def find(self, server_name: str, uri: str) -> McpResourceDescriptor | None:
        for resource in self._resources.get(server_name, []):
            if resource.uri == uri:
                return resource
        return None

