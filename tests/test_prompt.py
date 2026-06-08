import tempfile
import unittest
from pathlib import Path

from paicli_py.agent import Agent
from paicli_py.llm import ChatResponse
from paicli_py.prompt import PromptAssembler, PromptContext, PromptRepository
from paicli_py.skill import SkillContextBuffer, SkillRegistry
from paicli_py.tool import ToolRegistry


class CapturingLlm:
    def __init__(self):
        self.messages = []

    def chat(self, messages, tools):
        self.messages = list(messages)
        return ChatResponse("ok")


class PromptTest(unittest.TestCase):
    def test_prompt_assembly_order(self):
        prompt = PromptAssembler().assemble(PromptContext(
            project_context="project facts",
            skill_index="- demo: test skill",
        ))
        markers = [
            "## Identity",
            "## Personality",
            "## Mode",
            "## Approval Policy",
            "## Project Context",
            "## Available Skills",
            "## Context Management",
            "## Handoff",
        ]
        positions = [prompt.index(marker) for marker in markers]
        self.assertEqual(sorted(positions), positions)

    def test_project_prompt_overrides_builtin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_dir = root / ".paicli-py" / "prompts"
            prompt_dir.mkdir(parents=True)
            (prompt_dir / "base.md").write_text("## Identity\nProject override", encoding="utf-8")
            repository = PromptRepository(project_dir=root)
            self.assertIn("Project override", repository.read("base.md"))

    def test_builtin_prompt_modes_cover_planner_and_team(self):
        repository = PromptRepository()
        for mode in ["agent", "plan", "planner", "team-planner", "team-worker", "team-reviewer"]:
            self.assertTrue(repository.exists(f"modes/{mode}.md"))
            prompt = PromptAssembler(repository).assemble(PromptContext(mode=mode))
            self.assertIn("## Mode", prompt)
            if mode == "team-reviewer":
                self.assertIn('"approved"', prompt)
            if mode == "planner":
                self.assertIn('"tasks"', prompt)

    def test_suggest_approval_profile_is_available(self):
        prompt = PromptAssembler().assemble(PromptContext(approval="suggest"))
        self.assertIn("## Approval Policy", prompt)
        self.assertIn("human approval", prompt)

    def test_agent_uses_prompt_assembler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / ".paicli-py" / "skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo\ndescription: demo skill\n---\nbody",
                encoding="utf-8",
            )
            skill_registry = SkillRegistry(root)
            skill_registry.reload()
            llm = CapturingLlm()
            Agent(
                llm,
                ToolRegistry(root),
                skill_registry=skill_registry,
                skill_buffer=SkillContextBuffer(),
            ).run("hello")
            system = llm.messages[0].content
            self.assertIn("## Identity", system)
            self.assertIn("Available Skills", system)
            self.assertIn("demo skill", system)


if __name__ == "__main__":
    unittest.main()
