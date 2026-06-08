from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .client import McpClient
from .config import McpServerConfig
from .events import McpEvent
from .jsonrpc import JsonRpcTransport
from .prompts import McpPromptDescriptor
from .protocol import McpToolDescriptor
from .resources import McpResourceDescriptor


class McpServerStatus(str, Enum):
    STARTING = "starting"
    READY = "ready"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass
class McpServer:
    config: McpServerConfig
    transport: JsonRpcTransport | None = None
    client: McpClient | None = None
    status: McpServerStatus = McpServerStatus.DISABLED
    error_message: str = ""
    tools: list[McpToolDescriptor] = field(default_factory=list)
    resources: list[McpResourceDescriptor] = field(default_factory=list)
    prompts: list[McpPromptDescriptor] = field(default_factory=list)
    events: list[McpEvent] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.config.name

    def start(self, transport: JsonRpcTransport) -> None:
        if self.config.disabled:
            self.status = McpServerStatus.DISABLED
            self.log_event("info", "server.disabled", "server is disabled")
            return
        self.status = McpServerStatus.STARTING
        self.transport = transport
        self.client = McpClient(self.name, transport)
        self.log_event("info", "server.starting", "starting server")
        try:
            self.client.initialize()
            self.tools = self.client.list_tools()
            self.resources = self._safe_list_resources()
            self.prompts = self._safe_list_prompts()
            self.status = McpServerStatus.READY
            self.error_message = ""
            self.log_event(
                "info",
                "server.ready",
                "server ready",
                tools=len(self.tools),
                resources=len(self.resources),
                prompts=len(self.prompts),
            )
        except Exception as exc:
            self.status = McpServerStatus.ERROR
            self.error_message = str(exc)
            self.log_event("error", "server.error", str(exc))

    def close(self) -> None:
        transport = self.transport
        if transport is not None and hasattr(transport, "close"):
            transport.close()
        self.transport = None
        self.client = None
        self.tools = []
        self.resources = []
        self.prompts = []
        self.log_event("info", "server.closed", "server closed")

    def _safe_list_resources(self) -> list[McpResourceDescriptor]:
        if self.client is None:
            return []
        try:
            return self.client.list_resources()
        except Exception as exc:
            self.log_event("warn", "resources.list_failed", "resources/list failed", error=str(exc))
            return []

    def _safe_list_prompts(self) -> list[McpPromptDescriptor]:
        if self.client is None:
            return []
        try:
            return self.client.list_prompts()
        except Exception as exc:
            self.log_event("warn", "prompts.list_failed", "prompts/list failed", error=str(exc))
            return []

    def log_event(self, level: str, event: str, message: str, **details) -> McpEvent:
        record = McpEvent(level=level, event=event, message=message, details=details)
        self.events.append(record)
        self.logs.append(record.format())
        return record
