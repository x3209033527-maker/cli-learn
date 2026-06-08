import unittest

from paicli_py.memory import MemoryManager


class MemoryManagerTest(unittest.TestCase):
    def test_memory_status_and_fact_context(self):
        memory = MemoryManager(max_entries=2)
        memory.add_user_message("one")
        memory.add_assistant_message("two")
        memory.add_tool_result("read_file", "three")
        self.assertEqual(2, len(memory.short_term))
        memory.store_fact("project uses Python")
        self.assertIn("project uses Python", memory.context_for("Python project"))
        self.assertIn("short_term=2", memory.status())


if __name__ == "__main__":
    unittest.main()

