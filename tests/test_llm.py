import unittest

from paicli_py.llm import ImageContent, Message, OpenAICompatibleClient


class LlmSerializationTest(unittest.TestCase):
    def test_user_message_with_images_serializes_to_openai_content_parts(self):
        client = OpenAICompatibleClient("https://example.test", "key", "model")

        payload = client._message_to_json(Message.user("describe", [ImageContent("image/png", "abc", "low")]))

        self.assertEqual("user", payload["role"])
        self.assertEqual("text", payload["content"][0]["type"])
        self.assertEqual("describe", payload["content"][0]["text"])
        self.assertEqual("image_url", payload["content"][1]["type"])
        self.assertEqual("data:image/png;base64,abc", payload["content"][1]["image_url"]["url"])
        self.assertEqual("low", payload["content"][1]["image_url"]["detail"])

    def test_user_message_without_images_stays_plain_text(self):
        client = OpenAICompatibleClient("https://example.test", "key", "model")

        payload = client._message_to_json(Message.user("hello"))

        self.assertEqual("hello", payload["content"])


if __name__ == "__main__":
    unittest.main()
