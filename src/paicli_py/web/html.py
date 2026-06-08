from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin


BLOCK_TAGS = {"div", "section", "article", "main", "body"}
NOISE_TAGS = {
    "script",
    "style",
    "noscript",
    "iframe",
    "nav",
    "aside",
    "header",
    "footer",
    "form",
    "svg",
    "canvas",
    "button",
}
NOISE_MARKERS = {
    "ads",
    "advert",
    "banner",
    "popup",
    "modal",
    "subscribe",
    "newsletter",
    "related",
    "recommend",
    "comment",
    "share",
    "social",
    "breadcrumb",
    "sidebar",
    "promo",
    "cookie",
    "footer",
    "navigation",
}
VOID_TAGS = {"br", "hr", "img", "meta", "link", "input"}
WHITESPACE_PATTERN = re.compile(r"[ \t\r\f\v]+")
BLANK_LINES_PATTERN = re.compile(r"\n{3,}")


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["_Node | str"] = field(default_factory=list)


class _DomParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs):
        node = _Node(tag.lower(), {str(key).lower(): value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if node.tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag: str):
        normalized = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == normalized:
                del self.stack[index:]
                return

    def handle_data(self, data: str):
        if data:
            self.stack[-1].children.append(data)


def extract_text(html_text: str, max_chars: int = 8000, base_url: str = "") -> tuple[str, str]:
    parser = _DomParser()
    parser.feed(html_text)
    root = _clean_noise(parser.root)
    title = _pick_title(root)
    main = _pick_main(root)
    text = _collapse(_render_children(main, base_url) if main else "")
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n...(truncated)"
    return title, text


def _clean_noise(node: _Node) -> _Node:
    kept: list[_Node | str] = []
    for child in node.children:
        if isinstance(child, str):
            kept.append(child)
            continue
        if child.tag in NOISE_TAGS or _is_noise_marker(child):
            continue
        kept.append(_clean_noise(child))
    node.children = kept
    return node


def _is_noise_marker(node: _Node) -> bool:
    marker = f"{node.attrs.get('class', '')} {node.attrs.get('id', '')}".lower()
    return any(keyword in marker for keyword in NOISE_MARKERS)


def _pick_title(root: _Node) -> str:
    title = _first_text(root, "title")
    if title:
        return _inline_clean(title)
    h1 = _first_text(root, "h1")
    return _inline_clean(h1)


def _first_text(node: _Node, tag: str) -> str:
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag == tag:
                return _node_text(child)
            found = _first_text(child, tag)
            if found:
                return found
    return ""


def _pick_main(root: _Node) -> _Node | None:
    semantic = _find_semantic_main(root)
    if semantic is not None and len(_node_text(semantic)) > 80:
        return semantic
    candidates = _collect_candidates(root)
    if not candidates:
        return root
    return max(candidates, key=_score)


def _find_semantic_main(node: _Node) -> _Node | None:
    for child in node.children:
        if not isinstance(child, _Node):
            continue
        if child.tag in {"article", "main"} or child.attrs.get("role", "").lower() == "main":
            return child
        found = _find_semantic_main(child)
        if found is not None:
            return found
    return None


def _collect_candidates(node: _Node) -> list[_Node]:
    out = [node] if node.tag in BLOCK_TAGS else []
    for child in node.children:
        if isinstance(child, _Node):
            out.extend(_collect_candidates(child))
    return out


def _score(node: _Node) -> float:
    text_len = len(_node_text(node))
    if text_len < 80:
        return 0
    link_len = sum(len(_node_text(child)) for child in _iter_nodes(node) if child.tag == "a")
    penalty = min((link_len / max(text_len, 1)) * 2.0, 1.0)
    bonus = 1.2 if node.tag in {"article", "main"} or node.attrs.get("role", "").lower() == "main" else 1.0
    return text_len * (1.0 - penalty) * bonus


def _iter_nodes(node: _Node):
    yield node
    for child in node.children:
        if isinstance(child, _Node):
            yield from _iter_nodes(child)


def _render_children(node: _Node, base_url: str, inline: bool = False) -> str:
    parts = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(_inline_clean(child))
        else:
            parts.append(_render_node(child, base_url, inline))
    return "".join(parts)


def _render_node(node: _Node, base_url: str, inline: bool = False) -> str:
    tag = node.tag
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(tag[1])
        text = _inline_clean(_node_text(node))
        return f"\n\n{'#' * level} {text}\n\n" if text else ""
    if tag == "p":
        return f"\n\n{_render_children(node, base_url, True).strip()}\n\n"
    if tag == "br":
        return "\n"
    if tag == "hr":
        return "\n\n---\n\n"
    if tag in {"strong", "b"}:
        return f"**{_render_children(node, base_url, True).strip()}**"
    if tag in {"em", "i"}:
        return f"*{_render_children(node, base_url, True).strip()}*"
    if tag == "code":
        return f"`{_inline_clean(_node_text(node))}`"
    if tag == "pre":
        return f"\n\n```\n{_node_text(node).rstrip()}\n```\n\n"
    if tag == "blockquote":
        inner = _collapse(_render_children(node, base_url)).strip()
        quoted = "\n".join(f"> {line}" if line else ">" for line in inner.splitlines())
        return f"\n\n{quoted}\n\n"
    if tag == "ul":
        return _render_list(node, base_url, False)
    if tag == "ol":
        return _render_list(node, base_url, True)
    if tag == "li":
        return f"\n- {_render_children(node, base_url, True).strip()}"
    if tag == "a":
        text = _render_children(node, base_url, True).strip() or _inline_clean(_node_text(node))
        href = node.attrs.get("href", "").strip()
        if not text:
            return ""
        return f"[{text}]({urljoin(base_url, href)})" if href else text
    if tag == "img":
        return _inline_clean(node.attrs.get("alt", ""))
    if tag == "table":
        return _render_table(node)
    return _render_children(node, base_url, inline)


def _render_list(node: _Node, base_url: str, ordered: bool) -> str:
    lines = []
    index = 1
    for child in node.children:
        if not isinstance(child, _Node) or child.tag != "li":
            continue
        marker = f"{index}. " if ordered else "- "
        lines.append(marker + _render_children(child, base_url, True).strip().replace("\n", " "))
        index += 1
    return "\n\n" + "\n".join(lines) + "\n\n" if lines else ""


def _render_table(node: _Node) -> str:
    rows = []
    for row in [child for child in _iter_nodes(node) if child.tag == "tr"]:
        cells = [
            _inline_clean(_node_text(cell)).replace("|", "\\|")
            for cell in row.children
            if isinstance(cell, _Node) and cell.tag in {"th", "td"}
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    out = ["| " + " | ".join(rows[0]) + " |"]
    out.append("|" + "|".join(" --- " for _ in rows[0]) + "|")
    out.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n\n" + "\n".join(out) + "\n\n"


def _node_text(node: _Node) -> str:
    parts = []
    for child in node.children:
        parts.append(child if isinstance(child, str) else _node_text(child))
    return _inline_clean(" ".join(parts))


def _inline_clean(value: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", html.unescape(value)).strip()


def _collapse(value: str) -> str:
    lines = [WHITESPACE_PATTERN.sub(" ", line).strip() for line in value.splitlines()]
    return BLANK_LINES_PATTERN.sub("\n\n", "\n".join(lines)).strip()
