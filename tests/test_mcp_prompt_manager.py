import json
import tempfile
import unittest
from pathlib import Path

from paicli_py.mcp.manager import McpServerManager
from paicli_py.mcp.transport import InMemoryTransport
from paicli_py.tool import ToolRegistry


def prompt_transport(state):
    transport = InMemoryTransport()
    transport.on("initialize", lambda params: {})
    transport.on("tools/list", lambda params: {"tools": []})
    transport.on("resources/list", lambda params: {"resources": []})
    transport.on("prompts/list", lambda params: {
        "prompts": [{"name": state["prompt"], "description": "dynamic"}]
    })
    transport.on("prompts/get", lambda params: {
        "messages": [{"role": "user", "content": {"type": "text", "text": f"{params['name']}:{params.get('arguments', {}).get('file', '')}"}}]
    })
    return transport


class McpPromptManagerTest(unittest.TestCase):
    def test_manager_lists_gets_and_refreshes_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".paicli-py").mkdir()
            (root / ".paicli-py" / "mcp.json").write_text(json.dumps({
                "mcpServers": {"demo": {"command": "fake"}}
            }), encoding="utf-8")
            state = {"prompt": "old"}
            manager = McpServerManager(
                ToolRegistry(root),
                root,
                transport_factory=lambda config: prompt_transport(state),
            )
            manager.load_configured_servers()
            manager.start("demo")
            self.assertEqual("old", manager.list_prompts("demo")[0].name)
            self.assertEqual("old:", manager.get_prompt("demo", "old")[0].text)
            self.assertEqual("old:a.py", manager.get_prompt("demo", "old", {"file": "a.py"})[0].text)
            state["prompt"] = "new"
            self.assertIn("refreshed prompts", manager.handle_notification("demo", "notifications/prompts/list_changed"))
            self.assertEqual("new", manager.list_prompts("demo")[0].name)


if __name__ == "__main__":
    unittest.main()
