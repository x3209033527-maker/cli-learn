from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .frontmatter import parse_skill_markdown
from .state import SkillStateStore


@dataclass(frozen=True)
class SkillReference:
    path: str
    body: str


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: str
    enabled: bool = True
    references: tuple[SkillReference, ...] = ()

    def context_body(self) -> str:
        if not self.references:
            return self.body
        parts = [self.body.rstrip()]
        for reference in self.references:
            parts.append(f"<reference path=\"{reference.path}\">\n{reference.body.rstrip()}\n</reference>")
        return "\n\n".join(parts)


class SkillRegistry:
    def __init__(
        self,
        project_dir: str | Path,
        user_dir: str | Path | None = None,
        state_store: SkillStateStore | None = None,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.user_dir = Path(user_dir) if user_dir else Path.home() / ".paicli-py" / "skills"
        self.project_skills_dir = self.project_dir / ".paicli-py" / "skills"
        self.state_store = state_store or SkillStateStore()
        self._skills: dict[str, Skill] = {}

    def reload(self) -> None:
        skills: dict[str, Skill] = {}
        for skill in self._load_bundled():
            skills[skill.name] = skill
        for base in [self.user_dir, self.project_skills_dir]:
            for skill in self._load_from_base(base):
                skills[skill.name] = skill
        disabled = self.state_store.disabled()
        self._skills = {
            name: Skill(
                skill.name,
                skill.description,
                skill.body,
                skill.path,
                enabled=name not in disabled,
                references=skill.references,
            )
            for name, skill in skills.items()
        }

    def all_skills(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda item: item.name)

    def enabled_skills(self) -> list[Skill]:
        return [skill for skill in self.all_skills() if skill.enabled]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def set_enabled(self, name: str, enabled: bool) -> str:
        if name not in self._skills:
            return f"skill not found: {name}"
        self.state_store.set_enabled(name, enabled)
        skill = self._skills[name]
        self._skills[name] = Skill(
            skill.name,
            skill.description,
            skill.body,
            skill.path,
            enabled=enabled,
            references=skill.references,
        )
        return f"skill {'enabled' if enabled else 'disabled'}: {name}"

    def format_index(self, limit: int = 20) -> str:
        lines = []
        for skill in self.enabled_skills()[:limit]:
            lines.append(f"- {skill.name}: {skill.description}")
        return "\n".join(lines)

    def _load_from_base(self, base: Path) -> list[Skill]:
        if not base.exists():
            return []
        skills = []
        for skill_file in base.glob("*/SKILL.md"):
            markdown = skill_file.read_text(encoding="utf-8")
            metadata, body = parse_skill_markdown(markdown)
            fallback_name = skill_file.parent.name
            name = metadata.get("name") or fallback_name
            description = metadata.get("description") or _first_non_empty_line(body) or name
            references = tuple(_load_path_references(skill_file.parent / "references"))
            skills.append(Skill(name, description, body, str(skill_file), references=references))
        return skills

    def _load_bundled(self) -> list[Skill]:
        skills = []
        try:
            root = resources.files("paicli_py.skill.resources")
        except ModuleNotFoundError:
            return []
        for child in root.iterdir():
            if not child.is_dir():
                continue
            skill_file = child.joinpath("SKILL.md")
            if not skill_file.is_file():
                continue
            markdown = skill_file.read_text(encoding="utf-8")
            metadata, body = parse_skill_markdown(markdown)
            fallback_name = child.name
            name = metadata.get("name") or fallback_name
            description = metadata.get("description") or _first_non_empty_line(body) or name
            references = tuple(_load_bundled_references(child, fallback_name))
            skills.append(Skill(name, description, body, f"bundled:{fallback_name}/SKILL.md", references=references))
        return skills


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip("# ").strip()
        if stripped:
            return stripped
    return ""


def _load_path_references(reference_dir: Path) -> list[SkillReference]:
    if not reference_dir.exists():
        return []
    references = []
    for path in sorted(reference_dir.rglob("*.md")):
        relative = path.relative_to(reference_dir.parent).as_posix()
        references.append(SkillReference(relative, path.read_text(encoding="utf-8")))
    return references


def _load_bundled_references(skill_root, skill_name: str) -> list[SkillReference]:
    reference_root = skill_root.joinpath("references")
    if not reference_root.is_dir():
        return []
    references = []
    for item in sorted(_walk_bundled_markdown(reference_root), key=lambda ref: ref[0]):
        relative, body = item
        references.append(SkillReference(f"bundled:{skill_name}/{relative}", body))
    return references


def _walk_bundled_markdown(root, prefix: str = "references"):
    for child in root.iterdir():
        child_prefix = f"{prefix}/{child.name}"
        if child.is_dir():
            yield from _walk_bundled_markdown(child, child_prefix)
        elif child.name.endswith(".md") and child.is_file():
            yield child_prefix, child.read_text(encoding="utf-8")
