import json
import sys
import threading
import unittest

from paicli_py.mcp.jsonrpc import JsonRpcClient
from paicli_py.mcp.transport import StdioTransport


class McpStdioTransportTest(unittest.TestCase):
    def test_dispatches_notification_before_response(self):
        script = (
            "import json, sys\n"
            "request = json.loads(sys.stdin.readline())\n"
            "print(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'}), flush=True)\n"
            "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': {'ok': True}}), flush=True)\n"
        )
        transport = StdioTransport([sys.executable, "-u", "-c", script])
        calls = []
        transport.set_notification_handler(lambda message: calls.append(message))
        try:
            result = JsonRpcClient(transport).call("initialize")
        finally:
            transport.close()

        self.assertEqual({"ok": True}, result)
        self.assertEqual("notifications/tools/list_changed", calls[0]["method"])

    def test_dispatches_idle_notification_after_response(self):
        script = (
            "import json, sys, time\n"
            "request = json.loads(sys.stdin.readline())\n"
            "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': {'ok': True}}), flush=True)\n"
            "time.sleep(0.1)\n"
            "print(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/resources/list_changed'}), flush=True)\n"
            "time.sleep(0.5)\n"
        )
        transport = StdioTransport([sys.executable, "-u", "-c", script])
        event = threading.Event()
        calls = []

        def on_notification(message):
            calls.append(message)
            event.set()

        transport.set_notification_handler(on_notification)
        try:
            result = JsonRpcClient(transport).call("initialize")
            self.assertEqual({"ok": True}, result)
            self.assertTrue(event.wait(timeout=2))
        finally:
            transport.close()

        self.assertEqual("notifications/resources/list_changed", calls[0]["method"])


if __name__ == "__main__":
    unittest.main()
