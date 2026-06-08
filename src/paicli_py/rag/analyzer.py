from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodeRelation:
    from_file: str
    from_name: str
    to_file: str | None
    to_name: str
    relation_type: str


class CodeAnalyzer:
    IMPORT_PATTERN = re.compile(r"^\s*import\s+(?!static\s+)([A-Za-z_][\w.]*);", re.MULTILINE)
    TYPE_PATTERN = re.compile(
        r"\b(class|interface|enum|record)\s+([A-Za-z_][\w]*)"
        r"(?:\s+extends\s+([A-Za-z_][\w.<>]*))?"
        r"(?:\s+implements\s+([A-Za-z_][\w.<>,\s]*))?"
    )
    METHOD_PATTERN = re.compile(
        r"^\s*(?:public|protected|private)?\s*(?:static\s+)?[A-Za-z_][\w<>\[\], ?]*\s+([A-Za-z_][\w]*)\s*\([^;{}]*\)\s*(?:throws\s+[^{]+)?\{",
        re.MULTILINE,
    )
    CALL_PATTERN = re.compile(r"\b([A-Za-z_][\w]*)\s*\(")
    CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "return", "new", "throw", "super", "this"}
    JDK_PREFIXES = ("java.", "javax.", "jakarta.")

    def analyze_file(self, root: Path, file_path: Path) -> list[CodeRelation]:
        relative = str(file_path.resolve().relative_to(root.resolve()))
        if file_path.suffix != ".java":
            return []
        content = file_path.read_text(encoding="utf-8", errors="replace")
        relations: list[CodeRelation] = []
        relations.extend(self._imports(relative, content))
        type_spans = [(match.start(), match.group(2)) for match in self.TYPE_PATTERN.finditer(content)]
        relations.extend(self._type_relations(relative, content))
        relations.extend(self._contains(relative, content, type_spans))
        relations.extend(self._calls(relative, content, type_spans))
        return relations

    def _imports(self, relative: str, content: str) -> list[CodeRelation]:
        relations = []
        for match in self.IMPORT_PATTERN.finditer(content):
            qualified = match.group(1)
            if qualified.startswith(self.JDK_PREFIXES):
                continue
            relations.append(CodeRelation(relative, "file", None, qualified.split(".")[-1], "imports"))
        return relations

    def _type_relations(self, relative: str, content: str) -> list[CodeRelation]:
        relations = []
        for match in self.TYPE_PATTERN.finditer(content):
            name = match.group(2)
            if match.group(3):
                relations.append(CodeRelation(relative, name, None, _simple_type(match.group(3)), "extends"))
            if match.group(4):
                for item in match.group(4).split(","):
                    target = _simple_type(item.strip())
                    if target:
                        relations.append(CodeRelation(relative, name, None, target, "implements"))
        return relations

    def _contains(self, relative: str, content: str, type_spans: list[tuple[int, str]]) -> list[CodeRelation]:
        return [
            CodeRelation(relative, _owner_for(match.start(), type_spans), relative, f"{_owner_for(match.start(), type_spans)}.{match.group(1)}", "contains")
            for match in self.METHOD_PATTERN.finditer(content)
        ]

    def _calls(self, relative: str, content: str, type_spans: list[tuple[int, str]]) -> list[CodeRelation]:
        declared_methods = {match.group(1) for match in self.METHOD_PATTERN.finditer(content)}
        relations = []
        for match in self.CALL_PATTERN.finditer(content):
            name = match.group(1)
            if name in self.CONTROL_WORDS or name in declared_methods or name[:1].isupper():
                continue
            relations.append(CodeRelation(relative, _owner_for(match.start(), type_spans), None, name, "calls"))
        return _dedupe(relations)


def _simple_type(text: str) -> str:
    text = text.split("<", 1)[0].strip()
    return text.split(".")[-1].strip()


def _owner_for(position: int, type_spans: list[tuple[int, str]]) -> str:
    owner = "file"
    for start, name in type_spans:
        if start > position:
            break
        owner = name
    return owner


def _dedupe(relations: list[CodeRelation]) -> list[CodeRelation]:
    seen = set()
    result = []
    for relation in relations:
        key = (relation.from_file, relation.from_name, relation.to_name, relation.relation_type)
        if key in seen:
            continue
        seen.add(key)
        result.append(relation)
    return result
