from __future__ import annotations

from typing import Any

from .jsonrpc import JsonRpcClient, JsonRpcTransport
from .prompts import McpPromptDescriptor, McpPromptMessage, parse_prompt_arguments
from .protocol import McpContent, McpToolDescriptor, sanitize_schema
from .resources import McpResourceContent, McpResourceDescriptor


class McpClient:
    def __init__(self, server_name: str, transport: JsonRpcTransport):
        self.server_name = server_name
        self.rpc = JsonRpcClient(transport)
        self.initialized = False

    def initialize(self) -> dict[str, Any]:
        result = self.rpc.call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"sampling": {}},
            "clientInfo": {"name": "paicli-py", "version": "0.1.0"},
        })
        self.initialized = True
        return result or {}

    def list_tools(self) -> list[McpToolDescriptor]:
        result = self.rpc.call("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        descriptors = []
        for tool in tools:
            name = str(tool.get("name", ""))
            if not name:
                continue
            descriptors.append(McpToolDescriptor(
                server_name=self.server_name,
                name=name,
                description=str(tool.get("description", "")),
                input_schema=sanitize_schema(tool.get("inputSchema")),
            ))
        return descriptors

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> list[McpContent]:
        result = self.rpc.call("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        if not isinstance(result, dict):
            return []
        content = []
        for item in result.get("content", []) or []:
            if not isinstance(item, dict):
                continue
            content.append(McpContent(
                type=str(item.get("type", "text")),
                text=str(item.get("text", "")),
                data=str(item.get("data", "")),
                mime_type=str(item.get("mimeType", "")),
            ))
        return content

    def list_resources(self) -> list[McpResourceDescriptor]:
        result = self.rpc.call("resources/list", {})
        resources = result.get("resources", []) if isinstance(result, dict) else []
        descriptors = []
        for item in resources:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri", ""))
            if not uri:
                continue
            descriptors.append(McpResourceDescriptor(
                server_name=self.server_name,
                uri=uri,
                name=str(item.get("name", "")),
                description=str(item.get("description", "")),
                mime_type=str(item.get("mimeType", "")),
            ))
        return descriptors

    def read_resource(self, uri: str) -> list[McpResourceContent]:
        result = self.rpc.call("resources/read", {"uri": uri})
        contents = result.get("contents", []) if isinstance(result, dict) else []
        parsed = []
        for item in contents:
            if not isinstance(item, dict):
                continue
            item_uri = str(item.get("uri", uri))
            parsed.append(McpResourceContent(
                server_name=self.server_name,
                uri=item_uri,
                text=str(item.get("text", "")),
                blob=str(item.get("blob", "")),
                mime_type=str(item.get("mimeType", "")),
            ))
        return parsed

    def list_prompts(self) -> list[McpPromptDescriptor]:
        result = self.rpc.call("prompts/list", {})
        prompts = result.get("prompts", []) if isinstance(result, dict) else []
        descriptors = []
        for item in prompts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            if not name:
                continue
            descriptors.append(McpPromptDescriptor(
                server_name=self.server_name,
                name=name,
                description=str(item.get("description", "")),
                arguments=parse_prompt_arguments(item.get("arguments")),
            ))
        return descriptors

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> list[McpPromptMessage]:
        result = self.rpc.call("prompts/get", {
            "name": name,
            "arguments": arguments or {},
        })
        messages = result.get("messages", []) if isinstance(result, dict) else []
        parsed = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content", {}) if isinstance(message.get("content"), dict) else {}
            parsed.append(McpPromptMessage(
                role=str(message.get("role", "user")),
                content_type=str(content.get("type", "text")),
                text=str(content.get("text", "")),
                data=str(content.get("data", "")),
                mime_type=str(content.get("mimeType", "")),
            ))
        return parsed
