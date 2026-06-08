import tempfile
import unittest

from paicli_py.agent import Agent
from paicli_py.llm import ChatResponse, ImageContent, ToolCall
from paicli_py.tool import ToolRegistry


class FakeLlm:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                "",
                [ToolCall("call_1", "write_file", {"path": "answer.txt", "content": "42"})],
            )
        return ChatResponse("done")


class CapturingLlm:
    def __init__(self):
        self.messages = []

    def chat(self, messages, tools):
        self.messages.append(list(messages))
        return ChatResponse("seen")


class FakeImageExpander:
    def expand(self, text):
        return text + "\n<images />", [ImageContent("image/png", "abc")]


class AgentTest(unittest.TestCase):
    def test_react_tool_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = FakeLlm()
            agent = Agent(llm, ToolRegistry(tmp))
            self.assertEqual("done", agent.run("create a file"))
            self.assertEqual(2, llm.calls)

    def test_agent_attaches_images_to_user_message(self):
        llm = CapturingLlm()
        agent = Agent(llm, image_expander=FakeImageExpander())

        self.assertEqual("seen", agent.run("describe @image:a.png"))

        user_message = llm.messages[0][1]
        self.assertIn("<images />", user_message.content)
        self.assertEqual(1, len(user_message.images))
        self.assertEqual("image/png", user_message.images[0].mime_type)


if __name__ == "__main__":
    unittest.main()
