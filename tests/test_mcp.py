import tempfile
import unittest

from paicli_py.mcp import McpClient, McpContent, sanitize_schema
from paicli_py.mcp.jsonrpc import JsonRpcClient, JsonRpcError
from paicli_py.mcp.transport import InMemoryTransport
from paicli_py.tool import ToolInvocation, ToolRegistry


class McpTest(unittest.TestCase):
    def test_schema_sanitizer_removes_refs_and_collapses_any_of(self):
        schema = {
            "type": "object",
            "$defs": {"Unused": {"type": "string"}},
            "properties": {
                "path": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "x" * 500},
                "ref": {"$ref": "#/$defs/Unused"},
            },
        }
        sanitized = sanitize_schema(schema, max_description=20)
        self.assertNotIn("$defs", sanitized)
        self.assertEqual("string", sanitized["properties"]["path"]["type"])
        self.assertLessEqual(len(sanitized["properties"]["path"]["description"]), 20)

    def test_json_rpc_error(self):
        transport = InMemoryTransport()
        client = JsonRpcClient(transport)
        with self.assertRaises(JsonRpcError):
            client.call("missing")

    def test_mcp_client_lists_and_calls_tools(self):
        transport = InMemoryTransport()
        seen_initialize = {}
        transport.on("initialize", lambda params: seen_initialize.update(params) or {"serverInfo": {"name": "demo"}})
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

        client = McpClient("demo", transport)
        self.assertEqual("demo", client.initialize()["serverInfo"]["name"])
        self.assertIn("sampling", seen_initialize["capabilities"])
        tools = client.list_tools()
        self.assertEqual("mcp__demo__echo", tools[0].namespaced_name)
        content = client.call_tool("echo", {"text": "hello"})
        self.assertEqual([McpContent("text", text="hello")], content)

    def test_mcp_client_lists_and_reads_resources(self):
        transport = InMemoryTransport()
        transport.on("resources/list", lambda params: {
            "resources": [{
                "uri": "file://notes.md",
                "name": "notes",
                "description": "Project notes",
                "mimeType": "text/markdown",
            }]
        })
        transport.on("resources/read", lambda params: {
            "contents": [{
                "uri": params["uri"],
                "text": "hello resource",
                "mimeType": "text/markdown",
            }]
        })
        client = McpClient("demo", transport)
        resources = client.list_resources()
        self.assertEqual("file://notes.md", resources[0].uri)
        contents = client.read_resource("file://notes.md")
        self.assertEqual("hello resource", contents[0].text)

    def test_mcp_tool_registry_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = InMemoryTransport()
            transport.on("tools/list", lambda params: {
                "tools": [{
                    "name": "echo",
                    "description": "Echo text",
                    "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                }]
            })
            transport.on("tools/call", lambda params: {
                "content": [{"type": "text", "text": params["arguments"]["text"]}]
            })
            client = McpClient("demo", transport)
            descriptor = client.list_tools()[0]
            registry = ToolRegistry(tmp)
            registry.register_mcp_tool(descriptor, lambda args: client.call_tool(descriptor.name, args))
            result = registry.execute(ToolInvocation("1", "mcp__demo__echo", {"text": "hello"}))
            self.assertEqual("hello", result.result)


if __name__ == "__main__":
    unittest.main()
