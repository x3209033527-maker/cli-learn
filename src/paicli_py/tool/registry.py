from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from paicli_py.browser import BrowserService
from paicli_py.llm import ToolDefinition
from paicli_py.mcp.formatting import format_mcp_content
from paicli_py.mcp.protocol import McpToolDescriptor
from paicli_py.policy import CommandGuard, PathGuard, PolicyError
from paicli_py.rag import CodeRetriever
from paicli_py.snapshot import SnapshotService, format_snapshot_list
from paicli_py.skill import SkillContextBuffer, SkillRegistry
from paicli_py.web import WebFetcher, create_search_provider
from paicli_py.web.search import format_search_results


ToolHandler = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class ToolInvocation:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionResult:
    id: str
    name: str
    result: str


@dataclass(frozen=True)
class _RegisteredTool:
    definition: ToolDefinition
    handler: ToolHandler


class ToolRegistry:
    MAX_PARALLEL_TOOLS = 4
    MAX_COMMAND_OUTPUT_CHARS = 8000
    MAX_WRITE_BYTES = 5 * 1024 * 1024

    def __init__(
        self,
        project_path: str | Path | None = None,
        command_timeout: int = 60,
        skill_registry: SkillRegistry | None = None,
        skill_buffer: SkillContextBuffer | None = None,
        web_fetcher: WebFetcher | None = None,
        search_provider=None,
    ):
        self.project_path = Path(project_path or Path.cwd()).resolve()
        self.path_guard = PathGuard(self.project_path)
        self.command_guard = CommandGuard()
        self.command_timeout = command_timeout
        self.code_retriever = CodeRetriever(self.project_path)
        self.snapshot_service = SnapshotService(self.project_path)
        self.browser_service = BrowserService()
        self.skill_registry = skill_registry
        self.skill_buffer = skill_buffer
        self.web_fetcher = web_fetcher or WebFetcher()
        self.search_provider = search_provider or create_search_provider(project_path=self.project_path)
        self._tools: dict[str, _RegisteredTool] = {}
        self._register_builtin_tools()

    def tool_definitions(self) -> list[ToolDefinition]:
        return [tool.definition for tool in self._tools.values()]

    def execute(self, invocation: ToolInvocation) -> ToolExecutionResult:
        tool = self._tools.get(invocation.name)
        if tool is None:
            return ToolExecutionResult(invocation.id, invocation.name, f"unknown tool: {invocation.name}")
        try:
            return ToolExecutionResult(invocation.id, invocation.name, tool.handler(invocation.arguments))
        except PolicyError as exc:
            return ToolExecutionResult(invocation.id, invocation.name, f"policy denied: {exc}")
        except Exception as exc:  # pragma: no cover - defensive path
            return ToolExecutionResult(invocation.id, invocation.name, f"tool failed: {exc}")

    def execute_tool_for_cli(self, name: str, arguments: dict[str, Any]) -> str:
        return self.execute(ToolInvocation("cli", name, arguments)).result

    def execute_tools(self, invocations: list[ToolInvocation]) -> list[ToolExecutionResult]:
        if not invocations:
            return []
        results: list[ToolExecutionResult | None] = [None] * len(invocations)
        max_workers = min(len(invocations), self.MAX_PARALLEL_TOOLS)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(self.execute, invocation): index
                for index, invocation in enumerate(invocations)
            }
            for future in as_completed(future_to_index):
                results[future_to_index[future]] = future.result()
        return [result for result in results if result is not None]

    def register_tool(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        self._tools[definition.name] = _RegisteredTool(definition, handler)

    def register_mcp_tool(self, descriptor: McpToolDescriptor, invoker: Callable[[dict[str, Any]], Any]) -> None:
        def handler(args: dict[str, Any]) -> str:
            result = invoker(args)
            if isinstance(result, str):
                return result
            if isinstance(result, list):
                return format_mcp_content(result)
            return str(result)

        self.register_tool(
            ToolDefinition(
                descriptor.namespaced_name,
                descriptor.description,
                descriptor.input_schema,
            ),
            handler,
        )

    def unregister_tool(self, name: str) -> None:
        self._tools.pop(name, None)

    def _register_builtin_tools(self) -> None:
        self.register_tool(
            ToolDefinition(
                "read_file",
                "Read a file inside the project root.",
                _object_schema({"path": "string"}, ["path"]),
            ),
            self._read_file,
        )
        self.register_tool(
            ToolDefinition(
                "write_file",
                "Write a UTF-8 text file inside the project root. Single file limit is 5MB.",
                _object_schema({"path": "string", "content": "string"}, ["path", "content"]),
            ),
            self._write_file,
        )
        self.register_tool(
            ToolDefinition(
                "list_dir",
                "List directory entries inside the project root.",
                _object_schema({"path": "string"}, ["path"]),
            ),
            self._list_dir,
        )
        self.register_tool(
            ToolDefinition(
                "execute_command",
                "Execute a short command in the project root.",
                _object_schema({"command": "string"}, ["command"]),
            ),
            self._execute_command,
        )
        self.register_tool(
            ToolDefinition(
                "index_code",
                "Index the current project for code search.",
                _object_schema({}, []),
            ),
            self._index_code,
        )
        self.register_tool(
            ToolDefinition(
                "search_code",
                "Search indexed project code using keyword and local vector similarity.",
                _object_schema({"query": "string", "top_k": "integer"}, ["query"]),
            ),
            self._search_code,
        )
        self.register_tool(
            ToolDefinition(
                "load_skill",
                "Load a named skill manual into the next agent turn context.",
                _object_schema({"name": "string"}, ["name"]),
            ),
            self._load_skill,
        )
        self.register_tool(
            ToolDefinition(
                "web_fetch",
                "Fetch a public URL and extract readable text.",
                _object_schema({"url": "string"}, ["url"]),
            ),
            self._web_fetch,
        )
        self.register_tool(
            ToolDefinition(
                "web_search",
                "Search the web using the configured search provider.",
                _object_schema({"query": "string", "limit": "integer"}, ["query"]),
            ),
            self._web_search,
        )
        self.register_tool(
            ToolDefinition(
                "create_snapshot",
                "Create a project snapshot that can be restored later.",
                _object_schema({"label": "string"}, []),
            ),
            self._create_snapshot,
        )
        self.register_tool(
            ToolDefinition(
                "list_snapshots",
                "List recent project snapshots.",
                _object_schema({"limit": "integer"}, []),
            ),
            self._list_snapshots,
        )
        self.register_tool(
            ToolDefinition(
                "revert_snapshot",
                "Restore files from a project snapshot by id.",
                _object_schema({"id": "string"}, ["id"]),
            ),
            self._revert_snapshot,
        )
        self.register_tool(
            ToolDefinition(
                "browser_status",
                "Show browser session mode and remote debugging connectivity.",
                _object_schema({}, []),
            ),
            self._browser_status,
        )
        self.register_tool(
            ToolDefinition(
                "browser_connect",
                "Connect to a local Chrome remote debugging port.",
                _object_schema({"port": "integer"}, []),
            ),
            self._browser_connect,
        )
        self.register_tool(
            ToolDefinition(
                "browser_disconnect",
                "Switch browser session back to isolated mode.",
                _object_schema({}, []),
            ),
            self._browser_disconnect,
        )
        self.register_tool(
            ToolDefinition(
                "browser_tabs",
                "List tabs from the connected shared browser session.",
                _object_schema({}, []),
            ),
            self._browser_tabs,
        )

    def _read_file(self, args: dict[str, Any]) -> str:
        safe = self.path_guard.resolve_safe(str(args.get("path", "")))
        return safe.read_text(encoding="utf-8")

    def _write_file(self, args: dict[str, Any]) -> str:
        safe = self.path_guard.resolve_safe(str(args.get("path", "")))
        content = str(args.get("content", ""))
        size = len(content.encode("utf-8"))
        if size > self.MAX_WRITE_BYTES:
            raise PolicyError(f"content exceeds {self.MAX_WRITE_BYTES} bytes")
        safe.parent.mkdir(parents=True, exist_ok=True)
        safe.write_text(content, encoding="utf-8")
        return f"wrote file: {safe.relative_to(self.project_path)}"

    def _list_dir(self, args: dict[str, Any]) -> str:
        safe = self.path_guard.resolve_safe(str(args.get("path", ".")))
        entries = []
        for child in sorted(safe.iterdir(), key=lambda item: item.name.lower()):
            prefix = "[D]" if child.is_dir() else "[F]"
            entries.append(f"{prefix} {child.name}")
        return "\n".join(entries)

    def _execute_command(self, args: dict[str, Any]) -> str:
        command = str(args.get("command", ""))
        self.command_guard.validate(command)
        completed = subprocess.run(
            command,
            cwd=self.project_path,
            shell=True,
            capture_output=True,
            encoding="utf-8", errors="replace",
            timeout=self.command_timeout,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        if len(output) > self.MAX_COMMAND_OUTPUT_CHARS:
            output = output[: self.MAX_COMMAND_OUTPUT_CHARS] + "\n...(truncated)"
        return f"exit_code={completed.returncode}\n{output}".strip()

    def _index_code(self, args: dict[str, Any]) -> str:
        count = self.code_retriever.index()
        return f"indexed {count} code chunks"

    def _search_code(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "query cannot be empty"
        raw_top_k = args.get("top_k", 5)
        try:
            top_k = max(1, min(20, int(raw_top_k)))
        except (TypeError, ValueError):
            top_k = 5
        return self.code_retriever.format_results(self.code_retriever.search(query, top_k))

    def _load_skill(self, args: dict[str, Any]) -> str:
        if self.skill_registry is None or self.skill_buffer is None:
            return "skill system is not configured"
        name = str(args.get("name", "")).strip()
        skill = self.skill_registry.get(name)
        if skill is None:
            return f"skill not found: {name}"
        if not skill.enabled:
            return f"skill disabled: {name}"
        self.skill_buffer.add(skill.name, skill.context_body())
        return f"loaded skill: {skill.name}"

    def _web_fetch(self, args: dict[str, Any]) -> str:
        url = str(args.get("url", "")).strip()
        if not url:
            return "url cannot be empty"
        return self.web_fetcher.fetch(url).format()

    def _web_search(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "query cannot be empty"
        try:
            limit = max(1, min(10, int(args.get("limit", 5))))
        except (TypeError, ValueError):
            limit = 5
        return format_search_results(self.search_provider.search(query, limit))

    def _create_snapshot(self, args: dict[str, Any]) -> str:
        snapshot = self.snapshot_service.create(str(args.get("label", "")))
        return f"snapshot created: {snapshot.id}\nFiles: {snapshot.file_count}\nBytes: {snapshot.byte_count}"

    def _list_snapshots(self, args: dict[str, Any]) -> str:
        try:
            limit = max(1, min(100, int(args.get("limit", 20))))
        except (TypeError, ValueError):
            limit = 20
        return format_snapshot_list(self.snapshot_service.list(limit))

    def _revert_snapshot(self, args: dict[str, Any]) -> str:
        snapshot_id = str(args.get("id", "")).strip()
        if not snapshot_id:
            return "snapshot id cannot be empty"
        return self.snapshot_service.revert(snapshot_id)

    def _browser_status(self, args: dict[str, Any]) -> str:
        return self.browser_service.status()

    def _browser_connect(self, args: dict[str, Any]) -> str:
        try:
            port = int(args.get("port", 9222))
        except (TypeError, ValueError):
            port = 9222
        return self.browser_service.connect(port)

    def _browser_disconnect(self, args: dict[str, Any]) -> str:
        return self.browser_service.disconnect()

    def _browser_tabs(self, args: dict[str, Any]) -> str:
        return self.browser_service.tabs()


def _object_schema(properties: dict[str, str], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            name: {"type": type_name}
            for name, type_name in properties.items()
        },
        "required": required,
    }


def invocation_from_llm(tool_call) -> ToolInvocation:
    args = tool_call.arguments
    if isinstance(args, str):
        args = json.loads(args)
    return ToolInvocation(tool_call.id, tool_call.name, args)
