import tempfile
import unittest

from paicli_py.tool import ToolInvocation, ToolRegistry


class FakeBrowserService:
    def status(self):
        return "browser status"

    def connect(self, port=9222):
        return f"connect {port}"

    def disconnect(self):
        return "disconnect"

    def tabs(self):
        return "tabs"


class ToolRegistryTest(unittest.TestCase):
    def test_read_write_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(tmp)
            write = registry.execute(ToolInvocation("1", "write_file", {
                "path": "notes/hello.txt",
                "content": "hello",
            }))
            self.assertIn("wrote file", write.result)

            read = registry.execute(ToolInvocation("2", "read_file", {"path": "notes/hello.txt"}))
            self.assertEqual("hello", read.result)

            listing = registry.execute(ToolInvocation("3", "list_dir", {"path": "notes"}))
            self.assertIn("[F] hello.txt", listing.result)

    def test_parallel_results_keep_original_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(tmp)
            calls = [
                ToolInvocation("1", "execute_command", {"command": "python --version"}),
                ToolInvocation("2", "write_file", {"path": "a.txt", "content": "A"}),
                ToolInvocation("3", "read_file", {"path": "a.txt"}),
            ]
            results = registry.execute_tools(calls)
            self.assertEqual(["1", "2", "3"], [result.id for result in results])

    def test_code_index_and_search_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(tmp)
            registry.execute(ToolInvocation("1", "write_file", {
                "path": "demo.py",
                "content": "def greet(name):\n    return 'hello ' + name\n",
            }))
            indexed = registry.execute(ToolInvocation("2", "index_code", {}))
            self.assertIn("indexed", indexed.result)
            search = registry.execute(ToolInvocation("3", "search_code", {"query": "greet name"}))
            self.assertIn("demo.py", search.result)
            self.assertIn("greet", search.result)

    def test_snapshot_tools_create_list_and_revert(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(tmp)
            registry.execute(ToolInvocation("1", "write_file", {"path": "a.txt", "content": "one"}))
            created = registry.execute(ToolInvocation("2", "create_snapshot", {"label": "before"}))
            snapshot_id = created.result.splitlines()[0].split(": ", 1)[1]

            registry.execute(ToolInvocation("3", "write_file", {"path": "a.txt", "content": "two"}))
            listed = registry.execute(ToolInvocation("4", "list_snapshots", {}))
            reverted = registry.execute(ToolInvocation("5", "revert_snapshot", {"id": snapshot_id}))
            read = registry.execute(ToolInvocation("6", "read_file", {"path": "a.txt"}))

            self.assertIn(snapshot_id, listed.result)
            self.assertIn("restored snapshot", reverted.result)
            self.assertEqual("one", read.result)

    def test_browser_tools_delegate_to_service(self):
        registry = ToolRegistry()
        registry.browser_service = FakeBrowserService()

        self.assertEqual("browser status", registry.execute(ToolInvocation("1", "browser_status", {})).result)
        self.assertEqual("connect 9333", registry.execute(ToolInvocation("2", "browser_connect", {"port": 9333})).result)
        self.assertEqual("tabs", registry.execute(ToolInvocation("3", "browser_tabs", {})).result)
        self.assertEqual("disconnect", registry.execute(ToolInvocation("4", "browser_disconnect", {})).result)


if __name__ == "__main__":
    unittest.main()
