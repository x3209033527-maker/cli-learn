from __future__ import annotations

from dataclasses import dataclass

from .repository import PromptRepository


@dataclass(frozen=True)
class PromptContext:
    mode: str = "agent"
    personality: str = "calm"
    approval: str = "auto"
    project_context: str = ""
    skill_index: str = ""
    memory_context: str = ""
    handoff: str = ""


class PromptAssembler:
    ORDER = [
        "base",
        "personality",
        "mode",
        "approval",
        "project_context",
        "skills",
        "context_mgmt",
        "handoff",
    ]

    def __init__(self, repository: PromptRepository | None = None):
        self.repository = repository or PromptRepository()

    def assemble(self, context: PromptContext) -> str:
        sections = {
            "base": self.repository.read("base.md"),
            "personality": self.repository.read(f"personalities/{context.personality}.md"),
            "mode": self.repository.read(f"modes/{context.mode}.md"),
            "approval": self.repository.read(f"approvals/{context.approval}.md"),
            "project_context": _optional_section("Project Context", context.project_context),
            "skills": _optional_section("Available Skills", context.skill_index),
            "context_mgmt": self.repository.read("context/context-management.md"),
            "handoff": context.handoff or self.repository.read("handoff.md"),
        }
        return "\n\n".join(section.strip() for key in self.ORDER if (section := sections.get(key, "").strip()))


def _optional_section(title: str, content: str) -> str:
    content = content.strip()
    if not content:
        return ""
    return f"## {title}\n{content}"

