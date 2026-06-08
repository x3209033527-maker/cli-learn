import unittest

from paicli_py.mcp import McpClient, McpPromptMessage
from paicli_py.mcp.transport import InMemoryTransport


class McpPromptsTest(unittest.TestCase):
    def test_client_lists_and_gets_prompts(self):
        transport = InMemoryTransport()
        transport.on("prompts/list", lambda params: {
            "prompts": [{
                "name": "review",
                "description": "Review code",
                "arguments": [{"name": "file", "description": "file path", "required": True}],
            }]
        })
        transport.on("prompts/get", lambda params: {
            "messages": [{
                "role": "user",
                "content": {"type": "text", "text": f"review {params['arguments']['file']}"},
            }]
        })
        client = McpClient("demo", transport)
        prompts = client.list_prompts()
        self.assertEqual("review", prompts[0].name)
        self.assertEqual("file", prompts[0].arguments[0].name)
        self.assertTrue(prompts[0].arguments[0].required)
        messages = client.get_prompt("review", {"file": "a.py"})
        self.assertEqual([McpPromptMessage("user", "text", text="review a.py")], messages)


if __name__ == "__main__":
    unittest.main()
