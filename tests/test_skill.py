import tempfile
import unittest
from pathlib import Path

from paicli_py.agent import Agent
from paicli_py.llm import ChatResponse
from paicli_py.skill import SkillContextBuffer, SkillRegistry, SkillStateStore, parse_skill_markdown
from paicli_py.tool import ToolInvocation, ToolRegistry


class CapturingLlm:
    def __init__(self):
        self.messages = []

    def chat(self, messages, tools):
        self.messages = list(messages)
        return ChatResponse("ok")


class SkillTest(unittest.TestCase):
    def test_frontmatter_parser(self):
        metadata, body = parse_skill_markdown("---\nname: web-access\ndescription: Browse well\n---\n# Body")
        self.assertEqual("web-access", metadata["name"])
        self.assertEqual("Browse well", metadata["description"])
        self.assertEqual("# Body", body)

    def test_registry_project_overrides_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "user-skills"
            project_dir = root / ".paicli-py" / "skills"
            (user_dir / "demo").mkdir(parents=True)
            (project_dir / "demo").mkdir(parents=True)
            (user_dir / "demo" / "SKILL.md").write_text(
                "---\nname: demo\ndescription: user\n---\nuser body",
                encoding="utf-8",
            )
            (project_dir / "demo" / "SKILL.md").write_text(
                "---\nname: demo\ndescription: project\n---\nproject body",
                encoding="utf-8",
            )
            registry = SkillRegistry(root, user_dir=user_dir)
            registry.reload()
            self.assertEqual("project", registry.get("demo").description)
            self.assertEqual("project body", registry.get("demo").body)

    def test_registry_loads_bundled_web_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = SkillRegistry(Path(tmp), user_dir=Path(tmp) / "missing")
            registry.reload()
            skill = registry.get("web-access")
            self.assertIsNotNone(skill)
            self.assertTrue(skill.path.startswith("bundled:"))
            self.assertIn("web_fetch", skill.body)
            self.assertTrue(any("cdp-cheatsheet.md" in ref.path for ref in skill.references))

    def test_registry_loads_richer_bundled_skill_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = SkillRegistry(Path(tmp), user_dir=Path(tmp) / "missing")
            registry.reload()
            names = {skill.name for skill in registry.all_skills()}
            self.assertIn("code-review", names)
            self.assertIn("rag-indexing", names)
            self.assertIn("mcp-ops", names)
            review = registry.get("code-review")
            self.assertTrue(any("review-checklist.md" in ref.path for ref in review.references))

    def test_project_skill_overrides_bundled_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_skill = root / ".paicli-py" / "skills" / "web-access"
            project_skill.mkdir(parents=True)
            (project_skill / "SKILL.md").write_text(
                "---\nname: web-access\ndescription: project web\n---\nproject body",
                encoding="utf-8",
            )
            registry = SkillRegistry(root, user_dir=root / "missing")
            registry.reload()
            self.assertEqual("project web", registry.get("web-access").description)
            self.assertEqual("project body", registry.get("web-access").body)

    def test_load_skill_tool_buffers_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / ".paicli-py" / "skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo\ndescription: demo skill\n---\nRemember this rule.",
                encoding="utf-8",
            )
            registry = SkillRegistry(root)
            registry.reload()
            buffer = SkillContextBuffer()
            tools = ToolRegistry(root, skill_registry=registry, skill_buffer=buffer)
            result = tools.execute(ToolInvocation("1", "load_skill", {"name": "demo"}))
            self.assertIn("loaded skill", result.result)
            self.assertIn("Remember this rule.", buffer.drain())

    def test_load_skill_tool_buffers_reference_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / ".paicli-py" / "skills" / "demo"
            refs_dir = skill_dir / "references"
            refs_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo\ndescription: demo skill\n---\nMain body.",
                encoding="utf-8",
            )
            (refs_dir / "notes.md").write_text("Reference note.", encoding="utf-8")
            registry = SkillRegistry(root)
            registry.reload()
            buffer = SkillContextBuffer()
            tools = ToolRegistry(root, skill_registry=registry, skill_buffer=buffer)

            result = tools.execute(ToolInvocation("1", "load_skill", {"name": "demo"}))
            context = buffer.drain()

            self.assertIn("loaded skill", result.result)
            self.assertIn("Main body.", context)
            self.assertIn("<reference path=\"references/notes.md\">", context)
            self.assertIn("Reference note.", context)

    def test_disabled_skill_state_persists_and_blocks_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / ".paicli-py" / "skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo\ndescription: demo skill\n---\nbody",
                encoding="utf-8",
            )
            state_store = SkillStateStore(root / "skills.json")
            registry = SkillRegistry(root, state_store=state_store)
            registry.reload()
            self.assertIn("disabled", registry.set_enabled("demo", False))

            reloaded = SkillRegistry(root, state_store=state_store)
            reloaded.reload()
            self.assertFalse(reloaded.get("demo").enabled)
            tools = ToolRegistry(root, skill_registry=reloaded, skill_buffer=SkillContextBuffer())
            result = tools.execute(ToolInvocation("1", "load_skill", {"name": "demo"}))
            self.assertIn("skill disabled", result.result)

    def test_agent_injects_buffered_skill_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            buffer = SkillContextBuffer()
            buffer.add("demo", "Skill body")
            llm = CapturingLlm()
            Agent(llm, ToolRegistry(root), skill_buffer=buffer).run("hello")
            user_messages = [message for message in llm.messages if message.role == "user"]
            self.assertIn("<skill name=\"demo\">", user_messages[-1].content)
            self.assertIn("hello", user_messages[-1].content)


if __name__ == "__main__":
    unittest.main()
