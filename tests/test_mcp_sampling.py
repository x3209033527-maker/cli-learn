import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from paicli_py.mcp import (
    SamplingRejected,
    SamplingRequestParser,
    SamplingResult,
    default_sampling_handler,
    format_sampling_request,
)
from paicli_py.mcp.manager import McpServerManager
from paicli_py.mcp.transport import InMemoryTransport
from paicli_py.tool import ToolRegistry


def sampling_transport():
    transport = InMemoryTransport()
    transport.on("initialize", lambda params: {})
    transport.on("tools/list", lambda params: {"tools": []})
    transport.on("resources/list", lambda params: {"resources": []})
    transport.on("prompts/list", lambda params: {"prompts": []})
    return transport


class McpSamplingTest(unittest.TestCase):
    def test_sampling_parser_accepts_text_messages_and_bounds_tokens(self):
        request = SamplingRequestParser.parse("demo", {
            "systemPrompt": "Be brief",
            "maxTokens": 999999,
            "temperature": "0.2",
            "messages": [
                {"role": "user", "content": {"type": "text", "text": "hello"}},
            ],
        })

        self.assertEqual("demo", request.server_name)
        self.assertEqual(32768, request.max_tokens)
        self.assertEqual(0.2, request.temperature)
        self.assertEqual("hello", request.messages[0].text)
        self.assertIn("Be brief", format_sampling_request(request))

    def test_default_sampling_handler_rejects(self):
        request = SamplingRequestParser.parse("demo", {})

        with self.assertRaises(SamplingRejected):
            default_sampling_handler(request)

    def test_manager_handles_sampling_request_with_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".paicli-py").mkdir()
            (root / ".paicli-py" / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": "fake"}}
            }), encoding="utf-8")
            seen = []

            def handler(request):
                seen.append(request)
                return SamplingResult(text="sampled", model="unit-test")

            manager = McpServerManager(
                ToolRegistry(root),
                root,
                transport_factory=lambda config: sampling_transport(),
                sampling_handler=handler,
            )
            manager.load_configured_servers()
            manager.start("demo")

            result = manager.handle_sampling_request("demo", {
                "messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}]
            })

            self.assertEqual("sampled", result["content"]["text"])
            self.assertEqual("unit-test", result["model"])
            self.assertEqual("hi", seen[0].messages[0].text)
            self.assertIn("sampling.requested", [event.event for event in manager.servers["demo"].events])

    def test_in_memory_transport_routes_sampling_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".paicli-py").mkdir()
            (root / ".paicli-py" / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": "fake"}}
            }), encoding="utf-8")
            transport = sampling_transport()
            manager = McpServerManager(
                ToolRegistry(root),
                root,
                transport_factory=lambda config: transport,
                sampling_handler=lambda request: SamplingResult(text=request.messages[0].text.upper(), model="unit-test"),
            )
            manager.load_configured_servers()
            manager.start("demo")

            response = transport.emit_request("sampling/createMessage", {
                "messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}]
            }, request_id=42)

            self.assertEqual(42, response["id"])
            self.assertEqual("HI", response["result"]["content"]["text"])

    def test_stdio_transport_routes_sampling_request(self):
        script = (
            "import json, sys\n"
            "sent = False\n"
            "while True:\n"
            "    line = sys.stdin.readline()\n"
            "    if not line:\n"
            "        break\n"
            "    request = json.loads(line)\n"
            "    method = request.get('method')\n"
            "    if method == 'tools/list':\n"
            "        result = {'tools': []}\n"
            "    elif method == 'resources/list':\n"
            "        result = {'resources': []}\n"
            "    elif method == 'prompts/list':\n"
            "        result = {'prompts': []}\n"
            "        print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)\n"
            "        if not sent:\n"
            "            sent = True\n"
            "            print(json.dumps({'jsonrpc': '2.0', 'id': 77, 'method': 'sampling/createMessage', 'params': {'messages': [{'role': 'user', 'content': {'type': 'text', 'text': 'hi'}}]}}), flush=True)\n"
            "            response = json.loads(sys.stdin.readline())\n"
            "            if response.get('result', {}).get('content', {}).get('text') != 'sampled':\n"
            "                sys.exit(2)\n"
            "        continue\n"
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
            manager = McpServerManager(
                ToolRegistry(root),
                root,
                sampling_handler=lambda request: SamplingResult(text="sampled", model="unit-test"),
            )
            try:
                manager.load_configured_servers()
                manager.start("demo")

                deadline = time.time() + 3
                while time.time() < deadline:
                    events = [event.event for event in manager.servers["demo"].events]
                    if "sampling.completed" in events:
                        break
                    time.sleep(0.05)

                self.assertIn("sampling.completed", [event.event for event in manager.servers["demo"].events])
            finally:
                manager.close()

    def test_http_listener_routes_sampling_request(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                method = request.get("method")
                if method == "tools/list":
                    result = {"tools": []}
                elif method == "resources/list":
                    result = {"resources": []}
                elif method == "prompts/list":
                    result = {"prompts": []}
                else:
                    result = {}
                self._send_json({"jsonrpc": "2.0", "id": request["id"], "result": result})

            def do_GET(self):
                request = {
                    "jsonrpc": "2.0",
                    "id": 77,
                    "method": "sampling/createMessage",
                    "params": {"messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}]},
                }
                body = f"event: message\ndata: {json.dumps(request)}\n\n".encode("utf-8")
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
                manager = McpServerManager(
                    ToolRegistry(root),
                    root,
                    sampling_handler=lambda request: SamplingResult(text="sampled", model="unit-test"),
                )
                try:
                    manager.load_configured_servers()
                    manager.start("demo")

                    deadline = time.time() + 3
                    while time.time() < deadline:
                        events = [event.event for event in manager.servers["demo"].events]
                        if "sampling.completed" in events:
                            break
                        time.sleep(0.05)

                    self.assertIn("sampling.completed", [event.event for event in manager.servers["demo"].events])
                finally:
                    manager.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_sampling_notification_is_not_accepted_as_request(self):
        manager = McpServerManager(ToolRegistry(), Path.cwd())

        self.assertIn("must be sent", manager.handle_notification("demo", "sampling/createMessage"))


if __name__ == "__main__":
    unittest.main()
