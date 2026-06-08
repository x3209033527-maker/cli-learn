from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from paicli_py.tool import ToolRegistry

from .config import McpConfigLoader, McpServerConfig
from .formatting import format_mcp_content
from .jsonrpc import JsonRpcTransport
from .notifications import NotificationRouter
from .prompts import McpPromptDescriptor, McpPromptMessage
from .resources import McpResourceCache, McpResourceContent, McpResourceDescriptor
from .sampling import SamplingHandler, SamplingRejected, SamplingRequestParser, default_sampling_handler
from .server import McpServer, McpServerStatus
from .transport import StdioTransport, StreamableHttpTransport


TransportFactory = Callable[[McpServerConfig], JsonRpcTransport]


class McpServerManager:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        project_dir: str | Path,
        config_loader: McpConfigLoader | None = None,
        transport_factory: TransportFactory | None = None,
        sampling_handler: SamplingHandler | None = None,
    ):
        self.tool_registry = tool_registry
        self.project_dir = Path(project_dir).resolve()
        self.config_loader = config_loader or McpConfigLoader(self.project_dir)
        self.transport_factory = transport_factory or self._default_transport_factory
        self.sampling_handler = sampling_handler or default_sampling_handler
        self.servers: dict[str, McpServer] = {}
        self.resource_cache = McpResourceCache()
        self.notification_router = NotificationRouter(self._handle_transport_notification)
        self._restart_lock = threading.Lock()
        self._restart_in_progress: set[str] = set()

    def load_configured_servers(self) -> None:
        self.servers = {
            name: McpServer(config)
            for name, config in self.config_loader.load().items()
        }

    def start_all(self) -> None:
        for name in sorted(self.servers):
            self.start(name)

    def start(self, name: str) -> str:
        server = self.servers.get(name)
        if server is None:
            return f"MCP server not found: {name}"
        self._unregister_tools(server)
        if server.config.disabled:
            server.status = McpServerStatus.DISABLED
            server.log_event("info", "manager.start_skipped", "server disabled in config")
            return f"MCP server disabled: {name}"
        try:
            transport = self.transport_factory(server.config)
            self._bind_transport_events(server.name, transport)
            server.start(transport)
        except Exception as exc:
            server.status = McpServerStatus.ERROR
            server.error_message = str(exc)
            server.log_event("error", "manager.start_failed", str(exc))
        if server.status == McpServerStatus.READY:
            self._start_background_notifications(transport)
            self._register_tools(server)
            self.resource_cache.put(server.name, server.resources)
            server.log_event("info", "manager.tools_registered", "registered MCP tools", tools=len(server.tools))
            return f"MCP server ready: {name}"
        return f"MCP server error: {name} - {server.error_message}"

    def restart(self, name: str) -> str:
        server = self.servers.get(name)
        if server is None:
            return f"MCP server not found: {name}"
        self._unregister_tools(server)
        server.close()
        self.resource_cache.remove_server(name)
        server.config.disabled = False
        server.log_event("info", "manager.restart", "restarting server")
        return self.start(name)

    def disable(self, name: str) -> str:
        server = self.servers.get(name)
        if server is None:
            return f"MCP server not found: {name}"
        self._unregister_tools(server)
        server.close()
        self.resource_cache.remove_server(name)
        server.config.disabled = True
        server.status = McpServerStatus.DISABLED
        server.log_event("info", "manager.disabled", "server disabled")
        return f"MCP server disabled: {name}"

    def enable(self, name: str) -> str:
        server = self.servers.get(name)
        if server is None:
            return f"MCP server not found: {name}"
        server.config.disabled = False
        server.log_event("info", "manager.enabled", "server enabled")
        return self.start(name)

    def logs(self, name: str) -> str:
        server = self.servers.get(name)
        if server is None:
            return f"MCP server not found: {name}"
        return "\n".join(server.logs) if server.logs else f"MCP server has no logs: {name}"

    def format_status(self) -> str:
        if not self.servers:
            return "MCP servers: none"
        lines = ["MCP servers:"]
        for name in sorted(self.servers):
            server = self.servers[name]
            lines.append(f"- {name}: {server.status.value} tools={len(server.tools)} resources={len(server.resources)} prompts={len(server.prompts)}")
        return "\n".join(lines)

    def list_resources(self, server_name: str | None = None) -> list[McpResourceDescriptor]:
        return self.resource_cache.resources(server_name)

    def read_resource(self, server_name: str, uri: str) -> list[McpResourceContent]:
        server = self.servers.get(server_name)
        if server is None or server.client is None or server.status != McpServerStatus.READY:
            raise ValueError(f"MCP server is not ready: {server_name}")
        try:
            return server.client.read_resource(uri)
        except Exception as exc:
            self._handle_transport_failure(server_name, exc, source="resources.read")
            raise

    def handle_notification(self, server_name: str, method: str) -> str:
        if method == "notifications/tools/list_changed":
            return self.refresh_tools(server_name)
        if method in {"notifications/resources/list_changed", "notifications/resources/updated"}:
            return self.refresh_resources(server_name)
        if method == "notifications/prompts/list_changed":
            return self.refresh_prompts(server_name)
        if method == "sampling/createMessage":
            return "sampling requests must be sent as JSON-RPC requests"
        return f"ignored notification: {method}"

    def handle_sampling_request(self, server_name: str, params: dict | None = None) -> dict:
        server = self.servers.get(server_name)
        if server is None:
            raise SamplingRejected(f"MCP server not found: {server_name}")
        request = SamplingRequestParser.parse(server_name, params or {})
        server.log_event("info", "sampling.requested", "MCP sampling requested", messages=len(request.messages))
        result = self.sampling_handler(request)
        server.log_event("info", "sampling.completed", "MCP sampling completed", model=result.model)
        return result.to_mcp_result()

    def _handle_transport_notification(self, server_name: str, notification: dict) -> None:
        server = self.servers.get(server_name)
        method = str(notification.get("method", ""))
        if server is not None:
            server.log_event("info", "notification.received", "received MCP notification", method=method)
        try:
            result = self.handle_notification(server_name, method)
        except Exception as exc:
            self._handle_transport_failure(server_name, exc, source=f"notification:{method}")
            result = f"notification failed: {method} - {exc}"
        if server is not None:
            server.log_event("info", "notification.handled", result, method=method)

    def refresh_tools(self, server_name: str) -> str:
        server = self.servers.get(server_name)
        if server is None or server.client is None or server.status != McpServerStatus.READY:
            return f"MCP server is not ready: {server_name}"
        self._unregister_tools(server)
        server.tools = server.client.list_tools()
        self._register_tools(server)
        server.log_event("info", "tools.refreshed", "refreshed tools", tools=len(server.tools))
        return f"refreshed tools: {server_name}"

    def refresh_resources(self, server_name: str) -> str:
        server = self.servers.get(server_name)
        if server is None or server.client is None or server.status != McpServerStatus.READY:
            return f"MCP server is not ready: {server_name}"
        server.resources = server._safe_list_resources()
        self.resource_cache.put(server.name, server.resources)
        server.log_event("info", "resources.refreshed", "refreshed resources", resources=len(server.resources))
        return f"refreshed resources: {server_name}"

    def list_prompts(self, server_name: str | None = None) -> list[McpPromptDescriptor]:
        if server_name is not None:
            server = self.servers.get(server_name)
            return list(server.prompts) if server is not None else []
        prompts = []
        for server in self.servers.values():
            prompts.extend(server.prompts)
        return prompts

    def get_prompt(self, server_name: str, name: str, arguments: dict | None = None) -> list[McpPromptMessage]:
        server = self.servers.get(server_name)
        if server is None or server.client is None or server.status != McpServerStatus.READY:
            raise ValueError(f"MCP server is not ready: {server_name}")
        try:
            return server.client.get_prompt(name, arguments or {})
        except Exception as exc:
            self._handle_transport_failure(server_name, exc, source="prompts.get")
            raise

    def refresh_prompts(self, server_name: str) -> str:
        server = self.servers.get(server_name)
        if server is None or server.client is None or server.status != McpServerStatus.READY:
            return f"MCP server is not ready: {server_name}"
        server.prompts = server._safe_list_prompts()
        server.log_event("info", "prompts.refreshed", "refreshed prompts", prompts=len(server.prompts))
        return f"refreshed prompts: {server_name}"

    def close(self) -> None:
        for server in self.servers.values():
            self._unregister_tools(server)
            server.close()
            self.resource_cache.remove_server(server.name)

    def _register_tools(self, server: McpServer) -> None:
        if server.client is None:
            return
        for descriptor in server.tools:
            self.tool_registry.register_mcp_tool(
                descriptor,
                lambda args, descriptor=descriptor, server=server: self._invoke_mcp_tool(server, descriptor.name, args),
            )

    def _unregister_tools(self, server: McpServer) -> None:
        for descriptor in server.tools:
            self.tool_registry.unregister_tool(descriptor.namespaced_name)

    def _default_transport_factory(self, config: McpServerConfig) -> JsonRpcTransport:
        if config.is_stdio:
            return StdioTransport(config.command_line(), cwd=self.project_dir)
        if config.is_http:
            return StreamableHttpTransport(config.url, headers=config.http_headers())
        raise ValueError(f"MCP server {config.name} must define command or url")

    def _invoke_mcp_tool(self, server: McpServer, tool_name: str, args: dict) -> str:
        if server.client is None:
            raise RuntimeError(f"MCP server is not ready: {server.name}")
        try:
            return format_mcp_content(server.client.call_tool(tool_name, args))
        except Exception as exc:
            self._handle_transport_failure(server.name, exc, source=f"tools.call:{tool_name}")
            raise

    def _bind_transport_events(self, server_name: str, transport: JsonRpcTransport) -> None:
        set_handler = getattr(transport, "set_notification_handler", None)
        if callable(set_handler):
            set_handler(lambda message: self.notification_router.route(server_name, message))
        set_request_handler = getattr(transport, "set_request_handler", None)
        if callable(set_request_handler):
            set_request_handler(lambda message: self._handle_transport_request(server_name, message))
        set_failure_handler = getattr(transport, "set_failure_handler", None)
        if callable(set_failure_handler):
            set_failure_handler(lambda exc: self._handle_transport_failure(server_name, exc, source="transport"))

    def _start_background_notifications(self, transport: JsonRpcTransport) -> None:
        start_listener = getattr(transport, "start_notification_listener", None)
        if callable(start_listener):
            start_listener()

    def _handle_transport_failure(self, server_name: str, exc: BaseException, source: str) -> None:
        server = self.servers.get(server_name)
        if server is None or server.status == McpServerStatus.DISABLED:
            return
        if server.status == McpServerStatus.ERROR and server.error_message == str(exc):
            return
        self._unregister_tools(server)
        self.resource_cache.remove_server(server_name)
        server.status = McpServerStatus.ERROR
        server.error_message = str(exc)
        server.log_event("error", "transport.failure", "transport failure", source=source, error=str(exc))
        self._schedule_auto_restart(server)

    def _handle_transport_request(self, server_name: str, message: dict) -> dict:
        method = str(message.get("method", ""))
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "sampling/createMessage":
            return self.handle_sampling_request(server_name, params)
        raise RuntimeError(f"unsupported MCP client request: {method}")

    def _schedule_auto_restart(self, server: McpServer) -> None:
        if not server.config.auto_restart or server.config.disabled:
            return
        with self._restart_lock:
            if server.name in self._restart_in_progress:
                return
            self._restart_in_progress.add(server.name)
        server.log_event(
            "info",
            "manager.auto_restart_scheduled",
            "auto restart scheduled",
            delay_seconds=server.config.auto_restart_delay_seconds,
        )
        threading.Thread(
            target=self._auto_restart,
            args=(server.name,),
            name=f"paicli-mcp-auto-restart-{server.name}",
            daemon=True,
        ).start()

    def _auto_restart(self, server_name: str) -> None:
        try:
            server = self.servers.get(server_name)
            if server is not None and server.config.auto_restart_delay_seconds > 0:
                time.sleep(server.config.auto_restart_delay_seconds)
            server = self.servers.get(server_name)
            if server is None or server.config.disabled:
                return
            server.log_event("info", "manager.auto_restart", "auto restarting server")
            self.restart(server_name)
        finally:
            with self._restart_lock:
                self._restart_in_progress.discard(server_name)

