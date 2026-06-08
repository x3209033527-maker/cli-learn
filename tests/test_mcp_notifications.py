import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from paicli_py.mcp import NotificationRouter
from paicli_py.mcp.manager import McpServerManager
from paicli_py.mcp.transport import InMemoryTransport
from paicli_py.tool import ToolInvocation, ToolRegistry


def mutable_transport(state):
    transport = InMemoryTransport()
    transport.on("initialize", lambda params: {})
    transport.on("tools/list", lambda params: {
        "tools": [{
            "name": state["tool"],
            "description": "dynamic",
            "inputSchema": {"type": "object", "properties": {}},
        }]
    })
    transport.on("tools/call", lambda params: {
        "content": [{"type": "text", "text": params["name"]}]
    })
    transport.on("resources/list", lambda params: {
        "resources": [{"uri": state["resource"], "name": "resource"}]
    })
    return transport


class McpNotificationsTest(unittest.TestCase):
    def test_router_ignores_responses_and_routes_notifications(self):
        calls = []
        router = NotificationRouter(lambda server, notification: calls.append((server, notification["method"])))
        self.assertFalse(router.route("demo", {"id": 1, "result": {}}))
        self.assertTrue(router.route("demo", {"method": "notifications/tools/list_changed"}))
        self.assertEqual([("demo", "notifications/tools/list_changed")], calls)

    def test_manager_refreshes_tools_and_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".paicli-py").mkdir()
            (root / ".paicli-py" / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": "fake"}}
            }), encoding="utf-8")
            state = {"tool": "old", "resource": "file://old.md"}
            registry = ToolRegistry(root)
            manager = McpServerManager(
                registry,
                root,
                transport_factory=lambda config: mutable_transport(state),
            )
            manager.load_configured_servers()
            manager.start("demo")
            self.assertEqual("old", registry.execute(ToolInvocation("1", "mcp__demo__old", {})).result)
            self.assertEqual("file://old.md", manager.list_resources("demo")[0].uri)

            state["tool"] = "new"
            state["resource"] = "file://new.md"
            manager.handle_notification("demo", "notifications/tools/list_changed")
            manager.handle_notification("demo", "notifications/resources/list_changed")
            self.assertIn("unknown tool", registry.execute(ToolInvocation("2", "mcp__demo__old", {})).result)
            self.assertEqual("new", registry.execute(ToolInvocation("3", "mcp__demo__new", {})).result)
            self.assertEqual("file://new.md", manager.list_resources("demo")[0].uri)

    def test_manager_auto_refreshes_from_transport_notification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".paicli-py").mkdir()
            (root / ".paicli-py" / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": "fake"}}
            }), encoding="utf-8")
            state = {"tool": "old", "resource": "file://old.md"}
            transports = []
            registry = ToolRegistry(root)

            def factory(config):
                transport = mutable_transport(state)
                transports.append(transport)
                return transport

            manager = McpServerManager(registry, root, transport_factory=factory)
            manager.load_configured_servers()
            manager.start("demo")
            self.assertEqual("old", registry.execute(ToolInvocation("1", "mcp__demo__old", {})).result)

            state["tool"] = "new"
            transports[0].emit_notification("notifications/tools/list_changed")

            self.assertIn("unknown tool", registry.execute(ToolInvocation("2", "mcp__demo__old", {})).result)
            self.assertEqual("new", registry.execute(ToolInvocation("3", "mcp__demo__new", {})).result)
            self.assertIn("refreshed tools: demo", manager.logs("demo"))
            self.assertIn("notification.received", [event.event for event in manager.servers["demo"].events])
            self.assertIn("tools.refreshed", [event.event for event in manager.servers["demo"].events])

    def test_manager_auto_refreshes_from_idle_stdio_notification(self):
        script = (
            "import json, sys, threading, time\n"
            "tool_list_count = 0\n"
            "sent_notification = False\n"
            "def send_notification():\n"
            "    time.sleep(0.1)\n"
            "    print(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'}), flush=True)\n"
            "while True:\n"
            "    line = sys.stdin.readline()\n"
            "    if not line:\n"
            "        break\n"
            "    request = json.loads(line)\n"
            "    method = request.get('method')\n"
            "    if method == 'initialize':\n"
            "        result = {}\n"
            "    elif method == 'tools/list':\n"
            "        tool_list_count += 1\n"
            "        name = 'old' if tool_list_count == 1 else 'new'\n"
            "        result = {'tools': [{'name': name, 'description': 'dynamic', 'inputSchema': {'type': 'object'}}]}\n"
            "    elif method == 'tools/call':\n"
            "        result = {'content': [{'type': 'text', 'text': request['params']['name']}]}\n"
            "    elif method == 'resources/list':\n"
            "        result = {'resources': []}\n"
            "    elif method == 'prompts/list':\n"
            "        result = {'prompts': []}\n"
            "        if not sent_notification:\n"
            "            sent_notification = True\n"
            "            threading.Thread(target=send_notification, daemon=True).start()\n"
            "    else:\n"
            "        result = {}\n"
            "    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".paicli-py").mkdir()
            (root / ".paicli-py" / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": sys.executable, "args": ["-u", "-c", script]}}
            }), encoding="utf-8")
            registry = ToolRegistry(root)
            manager = McpServerManager(registry, root)
            try:
                manager.load_configured_servers()
                manager.start("demo")
                self.assertEqual("old", registry.execute(ToolInvocation("1", "mcp__demo__old", {})).result)

                deadline = time.time() + 3
                result = ""
                while time.time() < deadline:
                    result = registry.execute(ToolInvocation("2", "mcp__demo__new", {})).result
                    if result == "new":
                        break
                    time.sleep(0.05)

                self.assertEqual("new", result)
                self.assertIn("refreshed tools: demo", manager.logs("demo"))
            finally:
                manager.close()

    def test_manager_auto_refreshes_from_idle_http_notification(self):
        state = {"tool_list_count": 0}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                method = request.get("method")
                if method == "initialize":
                    result = {}
                elif method == "tools/list":
                    state["tool_list_count"] += 1
                    name = "old" if state["tool_list_count"] == 1 else "new"
                    result = {"tools": [{"name": name, "description": "dynamic", "inputSchema": {"type": "object"}}]}
                elif method == "tools/call":
                    result = {"content": [{"type": "text", "text": request["params"]["name"]}]}
                elif method == "resources/list":
                    result = {"resources": []}
                elif method == "prompts/list":
                    result = {"prompts": []}
                else:
                    result = {}
                self._send_json({"jsonrpc": "2.0", "id": request["id"], "result": result})

            def do_GET(self):
                time.sleep(0.1)
                notification = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
                body = f"event: message\ndata: {json.dumps(notification)}\n\n".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        host, port = httpd.server_address
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".paicli-py").mkdir()
                (root / ".paicli-py" / "mcp.json").write_text(json.dumps({
                    "mcpServers": {"demo": {"url": f"http://{host}:{port}/mcp"}}
                }), encoding="utf-8")
                registry = ToolRegistry(root)
                manager = McpServerManager(registry, root)
                try:
                    manager.load_configured_servers()
                    manager.start("demo")
                    self.assertEqual("old", registry.execute(ToolInvocation("1", "mcp__demo__old", {})).result)

                    deadline = time.time() + 3
                    result = ""
                    while time.time() < deadline:
                        result = registry.execute(ToolInvocation("2", "mcp__demo__new", {})).result
                        if result == "new":
                            break
                        time.sleep(0.05)

                    self.assertEqual("new", result)
                    self.assertIn("refreshed tools: demo", manager.logs("demo"))
                finally:
                    manager.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_manager_marks_error_from_http_listener_failure(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                method = request.get("method")
                if method == "tools/list":
                    result = {"tools": [{"name": "echo", "description": "dynamic", "inputSchema": {"type": "object"}}]}
                elif method == "resources/list":
                    result = {"resources": [{"uri": "file://notes.md", "name": "notes"}]}
                elif method == "prompts/list":
                    result = {"prompts": []}
                else:
                    result = {}
                self._send_json({"jsonrpc": "2.0", "id": request["id"], "result": result})

            def do_GET(self):
                body = b"event: message\ndata: not-json\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        host, port = httpd.server_address
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".paicli-py").mkdir()
                (root / ".paicli-py" / "mcp.json").write_text(json.dumps({
                    "mcpServers": {"demo": {"url": f"http://{host}:{port}/mcp"}}
                }), encoding="utf-8")
                registry = ToolRegistry(root)
                manager = McpServerManager(registry, root)
                try:
                    manager.load_configured_servers()
                    manager.start("demo")

                    deadline = time.time() + 3
                    while time.time() < deadline and manager.servers["demo"].status.value != "error":
                        time.sleep(0.05)

                    server = manager.servers["demo"]
                    self.assertEqual("error", server.status.value)
                    self.assertIn("transport.failure", [event.event for event in server.events])
                    self.assertEqual([], manager.list_resources("demo"))
                    self.assertIn("unknown tool", registry.execute(ToolInvocation("1", "mcp__demo__echo", {})).result)
                finally:
                    manager.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
