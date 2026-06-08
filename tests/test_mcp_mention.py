import json
import tempfile
import unittest
from pathlib import Path

from paicli_py.mcp.manager import McpServerManager
from paicli_py.mcp.mention import AtMentionExpander, parse_mentions
from paicli_py.mcp.transport import InMemoryTransport
from paicli_py.tool import ToolRegistry


def resource_transport():
    transport = InMemoryTransport()
    transport.on("initialize", lambda params: {})
    transport.on("tools/list", lambda params: {"tools": []})
    transport.on("resources/list", lambda params: {
        "resources": [{"uri": "file://notes.md", "name": "notes"}]
    })
    transport.on("resources/read", lambda params: {
        "contents": [{"uri": params["uri"], "text": "resource body"}]
    })
    return transport


class McpMentionTest(unittest.TestCase):
    def test_parse_mentions(self):
        mentions = parse_mentions("please read @demo:file://notes.md now")
        self.assertEqual("demo", mentions[0].server_name)
        self.assertEqual("file://notes.md", mentions[0].uri)

    def test_parse_mentions_skips_image_mentions(self):
        mentions = parse_mentions("look @image:screen.png and @demo:file://notes.md")
        self.assertEqual(1, len(mentions))
        self.assertEqual("demo", mentions[0].server_name)

    def test_expander_prepends_resource_block(self):
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
                transport_factory=lambda config: resource_transport(),
            )
            manager.load_configured_servers()
            manager.start("demo")
            expanded = AtMentionExpander(manager).expand("use @demo:file://notes.md")
            self.assertIn("<resource server=\"demo\" uri=\"file://notes.md\">", expanded)
            self.assertIn("resource body", expanded)
            self.assertIn("use @demo:file://notes.md", expanded)


if __name__ == "__main__":
    unittest.main()
