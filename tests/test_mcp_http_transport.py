import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from paicli_py.mcp.jsonrpc import JsonRpcClient
from paicli_py.mcp.transport import StreamableHttpTransport


class JsonRpcHandler(BaseHTTPRequestHandler):
    response_mode = "json"
    requests = []
    headers_seen = []

    def do_GET(self):
        self.__class__.headers_seen.append(dict(self.headers))
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/prompts/list_changed",
            "params": {"scope": "idle"},
        }
        body = f"event: message\ndata: {json.dumps(notification)}\n\n".encode("utf-8")
        self._send_sse(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append(payload)
        self.__class__.headers_seen.append(dict(self.headers))
        response = {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": {"method": payload["method"]},
        }
        if self.__class__.response_mode == "sse":
            body = f"event: message\ndata: {json.dumps(response)}\n\n".encode("utf-8")
            self._send_sse(body)
            return
        if self.__class__.response_mode == "sse_with_notification":
            notification = {
                "jsonrpc": "2.0",
                "method": "notifications/tools/list_changed",
                "params": {"scope": "test"},
            }
            body = (
                f"event: message\ndata: {json.dumps(notification)}\n\n"
                f"event: message\ndata: {json.dumps(response)}\n\n"
            ).encode("utf-8")
            self._send_sse(body)
            return
        if self.__class__.response_mode == "sse_with_request":
            request = {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "sampling/createMessage",
                "params": {"messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}]},
            }
            body = (
                f"event: message\ndata: {json.dumps(request)}\n\n"
                f"event: message\ndata: {json.dumps(response)}\n\n"
            ).encode("utf-8")
            self._send_sse(body)
            return
        if self.__class__.response_mode == "json_batch_with_notification":
            notification = {
                "jsonrpc": "2.0",
                "method": "notifications/resources/list_changed",
            }
            body = json.dumps([notification, response]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class McpHttpTransportTest(unittest.TestCase):
    def test_json_response(self):
        with _server("json") as url:
            result = JsonRpcClient(StreamableHttpTransport(url)).call("tools/list")
            self.assertEqual({"method": "tools/list"}, result)

    def test_headers_are_sent_on_post_and_get_listener(self):
        with _server("json") as url:
            event = threading.Event()
            transport = StreamableHttpTransport(url, headers={"Authorization": "Bearer token"})
            transport.set_notification_handler(lambda message: event.set())
            try:
                JsonRpcClient(transport).call("tools/list")
                transport.start_notification_listener()
                self.assertTrue(event.wait(timeout=2))
            finally:
                transport.close()

            self.assertTrue(all(headers.get("Authorization") == "Bearer token" for headers in JsonRpcHandler.headers_seen))

    def test_sse_response(self):
        with _server("sse") as url:
            result = JsonRpcClient(StreamableHttpTransport(url)).call("initialize")
            self.assertEqual({"method": "initialize"}, result)

    def test_sse_dispatches_notifications_before_response(self):
        with _server("sse_with_notification") as url:
            calls = []
            transport = StreamableHttpTransport(url)
            transport.set_notification_handler(lambda message: calls.append(message))

            result = JsonRpcClient(transport).call("initialize")

            self.assertEqual({"method": "initialize"}, result)
            self.assertEqual("notifications/tools/list_changed", calls[0]["method"])
            self.assertEqual({"scope": "test"}, calls[0]["params"])

    def test_sse_dispatches_server_requests_before_response(self):
        with _server("sse_with_request") as url:
            calls = []
            transport = StreamableHttpTransport(url)
            transport.set_request_handler(lambda message: calls.append(message) or {"ok": True})

            result = JsonRpcClient(transport).call("initialize")

            self.assertEqual({"method": "initialize"}, result)
            self.assertEqual("sampling/createMessage", calls[0]["method"])
            self.assertEqual("hi", calls[0]["params"]["messages"][0]["content"]["text"])

    def test_json_batch_dispatches_notifications_before_response(self):
        with _server("json_batch_with_notification") as url:
            calls = []
            transport = StreamableHttpTransport(url)
            transport.set_notification_handler(lambda message: calls.append(message))

            result = JsonRpcClient(transport).call("resources/list")

            self.assertEqual({"method": "resources/list"}, result)
            self.assertEqual("notifications/resources/list_changed", calls[0]["method"])

    def test_background_listener_dispatches_idle_sse_notifications(self):
        with _server("json") as url:
            event = threading.Event()
            calls = []
            transport = StreamableHttpTransport(url)
            transport.set_notification_handler(lambda message: (calls.append(message), event.set()))
            try:
                transport.start_notification_listener()
                self.assertTrue(event.wait(timeout=2))
            finally:
                transport.close()

            self.assertEqual("notifications/prompts/list_changed", calls[0]["method"])
            self.assertEqual({"scope": "idle"}, calls[0]["params"])


class _server:
    def __init__(self, mode):
        self.mode = mode
        self.httpd = None
        self.thread = None

    def __enter__(self):
        JsonRpcHandler.response_mode = self.mode
        JsonRpcHandler.requests = []
        JsonRpcHandler.headers_seen = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), JsonRpcHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/mcp"

    def __exit__(self, exc_type, exc, tb):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
