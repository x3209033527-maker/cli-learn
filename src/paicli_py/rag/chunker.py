from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodeChunk:
    file_path: str
    chunk_type: str
    name: str
    content: str
    start_line: int
    end_line: int

    def embedding_text(self) -> str:
        return f"{self.file_path}\n{self.chunk_type}: {self.name}\n{self.content}"


class CodeChunker:
    MAX_CHARS = 2000
    JAVA_CLASS_PATTERN = re.compile(r"\b(class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)")
    JAVA_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "try", "do", "synchronized"}

    def chunk_file(self, root: Path, file_path: Path) -> list[CodeChunk]:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        relative = str(file_path.resolve().relative_to(root.resolve()))
        if file_path.suffix == ".java":
            chunks = self._chunk_java(relative, content)
            if chunks:
                return chunks
        return self._chunk_large_text(relative, content)

    def _chunk_java(self, relative: str, content: str) -> list[CodeChunk]:
        lines = content.splitlines()
        sanitized = _sanitize_java(content)
        line_starts = _line_starts(content)
        chunks: list[CodeChunk] = []
        for class_match in self.JAVA_CLASS_PATTERN.finditer(sanitized):
            class_name = class_match.group(2)
            class_open = sanitized.find("{", class_match.end())
            if class_open < 0:
                continue
            class_close = _find_matching_brace(sanitized, class_open)
            if class_close < 0:
                class_close = len(sanitized) - 1
            class_start_line = _line_number(line_starts, class_match.start())
            class_end_line = _line_number(line_starts, class_close)
            header_end = min(class_start_line + 5, class_end_line, len(lines))
            header = "\n".join(lines[class_start_line - 1:header_end])
            chunks.append(CodeChunk(relative, "class", class_name, header.strip(), class_start_line, class_end_line))
            chunks.extend(self._method_chunks(relative, class_name, content, sanitized, line_starts, class_open, class_close))
        return chunks

    def _method_chunks(
        self,
        relative: str,
        class_name: str,
        content: str,
        sanitized: str,
        line_starts: list[int],
        class_open: int,
        class_close: int,
    ) -> list[CodeChunk]:
        chunks = []
        depth = 1
        boundary = class_open + 1
        index = class_open + 1
        while index < class_close:
            char = sanitized[index]
            if char == "{":
                if depth == 1:
                    signature = sanitized[boundary:index].strip()
                    method_name = self._method_name(signature, class_name)
                    if method_name:
                        method_close = _find_matching_brace(sanitized, index)
                        if method_close < 0:
                            method_close = min(len(sanitized) - 1, index + self.MAX_CHARS)
                        start = _signature_start(content, boundary, index)
                        start_line = _line_number(line_starts, start)
                        end_line = _line_number(line_starts, method_close)
                        method_content = "\n".join(content.splitlines()[start_line - 1:end_line])
                        chunks.append(CodeChunk(
                            relative,
                            "method",
                            f"{class_name}.{method_name}",
                            method_content.strip(),
                            start_line,
                            end_line,
                        ))
                        index = method_close + 1
                        boundary = index
                        depth = 1
                        continue
                depth += 1
            elif char == "}":
                depth -= 1
                boundary = index + 1
            elif depth == 1 and char == ";":
                boundary = index + 1
            index += 1
        return chunks

    def _method_name(self, signature: str, class_name: str) -> str:
        if "(" not in signature or ")" not in signature or "=" in signature:
            return ""
        compact = " ".join(line.strip() for line in signature.splitlines() if not line.strip().startswith("@"))
        paren = compact.rfind("(")
        before = compact[:paren].strip()
        identifiers = self.JAVA_IDENTIFIER_PATTERN.findall(before)
        if not identifiers:
            return ""
        name = identifiers[-1]
        if name in self.CONTROL_WORDS:
            return ""
        if name == "class" or name == "interface" or name == "enum" or name == "record":
            return ""
        return name

    def _chunk_large_text(self, relative: str, content: str) -> list[CodeChunk]:
        lines = content.splitlines()
        if not lines:
            return [CodeChunk(relative, "file", relative, "", 1, 1)]
        chunks: list[CodeChunk] = []
        buffer: list[str] = []
        start = 1
        for index, line in enumerate(lines, start=1):
            pending_size = sum(len(part) + 1 for part in buffer) + len(line)
            if buffer and pending_size > self.MAX_CHARS:
                chunks.append(CodeChunk(relative, "file", f"{relative}#{len(chunks) + 1}", "\n".join(buffer).strip(), start, index - 1))
                buffer = []
                start = index
            buffer.append(line)
        if buffer:
            chunks.append(CodeChunk(relative, "file", f"{relative}#{len(chunks) + 1}", "\n".join(buffer).strip(), start, len(lines)))
        return chunks


def _sanitize_java(content: str) -> str:
    result = []
    index = 0
    state = "code"
    while index < len(content):
        char = content[index]
        nxt = content[index + 1] if index + 1 < len(content) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                result.extend("  ")
                index += 2
                state = "line_comment"
                continue
            if char == "/" and nxt == "*":
                result.extend("  ")
                index += 2
                state = "block_comment"
                continue
            if char == '"':
                result.append(" ")
                index += 1
                state = "string"
                continue
            if char == "'":
                result.append(" ")
                index += 1
                state = "char"
                continue
            result.append(char)
        elif state == "line_comment":
            result.append("\n" if char == "\n" else " ")
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            result.append("\n" if char == "\n" else " ")
            if char == "*" and nxt == "/":
                result.append(" ")
                index += 1
                state = "code"
        elif state == "string":
            result.append("\n" if char == "\n" else " ")
            if char == "\\":
                if nxt:
                    result.append("\n" if nxt == "\n" else " ")
                    index += 1
            elif char == '"':
                state = "code"
        elif state == "char":
            result.append("\n" if char == "\n" else " ")
            if char == "\\":
                if nxt:
                    result.append("\n" if nxt == "\n" else " ")
                    index += 1
            elif char == "'":
                state = "code"
        index += 1
    return "".join(result)


def _find_matching_brace(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _line_starts(content: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(content):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _line_number(line_starts: list[int], offset: int) -> int:
    line = 1
    for index, start in enumerate(line_starts, start=1):
        if start > offset:
            break
        line = index
    return line


def _signature_start(content: str, boundary: int, open_brace: int) -> int:
    start = boundary
    lines = content[boundary:open_brace].splitlines()
    consumed = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("@") or stripped == "":
            break
        consumed += len(line) + 1
    if consumed:
        start = boundary + max(0, consumed - len(lines[-1]) - 1)
    while start > 0 and content[start - 1] not in ";\n{}":
        start -= 1
    return start
