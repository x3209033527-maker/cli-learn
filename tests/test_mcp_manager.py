import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from paicli_py.mcp import McpConfigLoader, McpServerStatus
from paicli_py.mcp.config import McpServerConfig
from paicli_py.mcp.manager import McpServerManager
from paicli_py.mcp.transport import InMemoryTransport, StreamableHttpTransport
from paicli_py.tool import ToolInvocation, ToolRegistry


def demo_transport():
    transport = InMemoryTransport()
    transport.on("initialize", lambda params: {"serverInfo": {"name": "demo"}})
    transport.on("tools/list", lambda params: {
        "tools": [{
            "name": "echo",
            "description": "Echo text",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }]
    })
    transport.on("tools/call", lambda params: {
        "content": [{"type": "text", "text": params["arguments"]["text"]}]
    })
    transport.on("resources/list", lambda params: {
        "resources": [{
            "uri": "file://notes.md",
            "name": "notes",
            "mimeType": "text/markdown",
        }]
    })
    transport.on("resources/read", lambda params: {
        "contents": [{
            "uri": params["uri"],
            "text": "resource body",
            "mimeType": "text/markdown",
        }]
    })
    return transport


def failing_call_transport():
    transport = demo_transport()
    transport.on("tools/call", lambda params: (_ for _ in ()).throw(RuntimeError("call boom")))
    return transport


class McpServerManagerTest(unittest.TestCase):
    def test_config_loader_project_overrides_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_config = root / "user-mcp.json"
            project_config_dir = root / ".paicli-py"
            project_config_dir.mkdir()
            user_config.write_text(json.dumps({
                "mcpServers": {
                    "demo": {"command": "old", "args": ["a"]},
                    "user-only": {"command": "user"},
                }
            }), encoding="utf-8")
            (project_config_dir / "mcp.json").write_text(json.dumps({
                "mcpServers": {
                    "demo": {"command": "new", "args": ["b"], "autoRestart": True, "autoRestartDelaySeconds": 0},
                }
            }), encoding="utf-8")
            configs = McpConfigLoader(root, user_config=user_config).load()
            self.assertEqual("new", configs["demo"].command)
            self.assertEqual(["b"], configs["demo"].args)
            self.assertTrue(configs["demo"].auto_restart)
            self.assertEqual(0, configs["demo"].auto_restart_delay_seconds)
            self.assertIn("user-only", configs)

    def test_start_registers_tool_and_disable_unregisters_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / ".paicli-py"
            config_dir.mkdir()
            (config_dir / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": "fake"}}
            }), encoding="utf-8")
            registry = ToolRegistry(root)
            manager = McpServerManager(
                registry,
                root,
                transport_factory=lambda config: demo_transport(),
            )
            manager.load_configured_servers()
            self.assertIn("ready", manager.start("demo"))
            self.assertEqual(McpServerStatus.READY, manager.servers["demo"].status)
            events = [event.event for event in manager.servers["demo"].events]
            self.assertIn("server.ready", events)
            self.assertIn("manager.tools_registered", events)
            self.assertEqual(1, len(manager.list_resources("demo")))
            result = registry.execute(ToolInvocation("1", "mcp__demo__echo", {"text": "hello"}))
            self.assertEqual("hello", result.result)

            manager.disable("demo")
            self.assertEqual(McpServerStatus.DISABLED, manager.servers["demo"].status)
            self.assertIn("manager.disabled", [event.event for event in manager.servers["demo"].events])
            self.assertEqual([], manager.list_resources("demo"))
            missing = registry.execute(ToolInvocation("2", "mcp__demo__echo", {"text": "hello"}))
            self.assertIn("unknown tool", missing.result)

    def test_restart_registers_tool_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / ".paicli-py"
            config_dir.mkdir()
            (config_dir / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": "fake"}}
            }), encoding="utf-8")
            registry = ToolRegistry(root)
            manager = McpServerManager(
                registry,
                root,
                transport_factory=lambda config: demo_transport(),
            )
            manager.load_configured_servers()
            manager.start("demo")
            manager.restart("demo")
            result = registry.execute(ToolInvocation("1", "mcp__demo__echo", {"text": "again"}))
            self.assertEqual("again", result.result)

    def test_default_transport_factory_uses_http_for_url_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = McpServerManager(ToolRegistry(tmp), tmp)
            transport = manager._default_transport_factory(McpServerConfig("remote", url="https://example.com/mcp"))
            self.assertIsInstance(transport, StreamableHttpTransport)

    def test_http_config_builds_headers_from_oauth_token(self):
        config = McpServerConfig.from_json("remote", {
            "url": "https://example.com/mcp",
            "headers": {"X-Test": "yes"},
            "oauth": {"accessToken": "secret"},
        })

        self.assertEqual("yes", config.http_headers()["X-Test"])
        self.assertEqual("Bearer secret", config.http_headers()["Authorization"])

    def test_http_config_builds_headers_from_oauth_token_env(self):
        old = os.environ.get("PAICLI_TEST_MCP_TOKEN")
        os.environ["PAICLI_TEST_MCP_TOKEN"] = "from-env"
        try:
            config = McpServerConfig.from_json("remote", {
                "url": "https://example.com/mcp",
                "oauth": {"tokenEnv": "PAICLI_TEST_MCP_TOKEN"},
            })
            self.assertEqual("Bearer from-env", config.http_headers()["Authorization"])
        finally:
            if old is None:
                os.environ.pop("PAICLI_TEST_MCP_TOKEN", None)
            else:
                os.environ["PAICLI_TEST_MCP_TOKEN"] = old

    def test_default_http_transport_receives_oauth_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = McpServerManager(ToolRegistry(tmp), tmp)
            config = McpServerConfig.from_json("remote", {
                "url": "https://example.com/mcp",
                "oauth": {"accessToken": "secret"},
            })
            transport = manager._default_transport_factory(config)

            self.assertEqual("Bearer secret", transport.headers["Authorization"])

    def test_structured_events_preserve_log_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / ".paicli-py"
            config_dir.mkdir()
            (config_dir / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": "fake"}}
            }), encoding="utf-8")
            registry = ToolRegistry(root)
            manager = McpServerManager(
                registry,
                root,
                transport_factory=lambda config: demo_transport(),
            )
            manager.load_configured_servers()
            manager.start("demo")
            server = manager.servers["demo"]

            self.assertTrue(all(event.level for event in server.events))
            self.assertIn("[info] server.ready: server ready", manager.logs("demo"))
            self.assertIn("tools=1", manager.logs("demo"))

    def test_start_failure_records_structured_error_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / ".paicli-py"
            config_dir.mkdir()
            (config_dir / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": "fake"}}
            }), encoding="utf-8")
            manager = McpServerManager(
                ToolRegistry(root),
                root,
                transport_factory=lambda config: (_ for _ in ()).throw(RuntimeError("boom")),
            )
            manager.load_configured_servers()

            self.assertIn("boom", manager.start("demo"))
            server = manager.servers["demo"]
            self.assertEqual(McpServerStatus.ERROR, server.status)
            self.assertEqual("manager.start_failed", server.events[-1].event)
            self.assertIn("[error] manager.start_failed: boom", manager.logs("demo"))

    def test_tool_call_failure_marks_server_error_and_unregisters_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / ".paicli-py"
            config_dir.mkdir()
            (config_dir / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": "fake"}}
            }), encoding="utf-8")
            registry = ToolRegistry(root)
            manager = McpServerManager(
                registry,
                root,
                transport_factory=lambda config: failing_call_transport(),
            )
            manager.load_configured_servers()
            manager.start("demo")

            result = registry.execute(ToolInvocation("1", "mcp__demo__echo", {"text": "hello"}))

            self.assertIn("tool failed", result.result)
            server = manager.servers["demo"]
            self.assertEqual(McpServerStatus.ERROR, server.status)
            self.assertIn("transport.failure", [event.event for event in server.events])
            self.assertEqual([], manager.list_resources("demo"))
            missing = registry.execute(ToolInvocation("2", "mcp__demo__echo", {"text": "hello"}))
            self.assertIn("unknown tool", missing.result)

    def test_auto_restart_restores_server_after_transport_failure_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / ".paicli-py"
            config_dir.mkdir()
            (config_dir / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": "fake", "autoRestart": True}}
            }), encoding="utf-8")
            registry = ToolRegistry(root)
            transports = [failing_call_transport(), demo_transport()]
            manager = McpServerManager(
                registry,
                root,
                transport_factory=lambda config: transports.pop(0),
            )
            manager.load_configured_servers()
            manager.start("demo")

            failed = registry.execute(ToolInvocation("1", "mcp__demo__echo", {"text": "hello"}))
            self.assertIn("tool failed", failed.result)

            deadline = time.time() + 3
            recovered = ""
            while time.time() < deadline:
                recovered = registry.execute(ToolInvocation("2", "mcp__demo__echo", {"text": "again"})).result
                if recovered == "again":
                    break
                time.sleep(0.05)

            server = manager.servers["demo"]
            self.assertEqual("again", recovered)
            self.assertEqual(McpServerStatus.READY, server.status)
            events = [event.event for event in server.events]
            self.assertIn("manager.auto_restart_scheduled", events)
            self.assertIn("manager.auto_restart", events)


if __name__ == "__main__":
    unittest.main()
