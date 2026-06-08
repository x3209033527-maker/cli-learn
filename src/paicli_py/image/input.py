from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

from paicli_py.llm import ImageContent
from paicli_py.policy import PathGuard, PolicyError


IMAGE_MENTION_PATTERN = re.compile(r"@image:([^\s]+)")
SUPPORTED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


@dataclass(frozen=True)
class ImageReference:
    raw: str
    path: str


def parse_image_references(text: str) -> list[ImageReference]:
    return [
        ImageReference(match.group(0), match.group(1))
        for match in IMAGE_MENTION_PATTERN.finditer(text or "")
    ]


class ImageInputExpander:
    MAX_IMAGES = 6
    MAX_IMAGE_BYTES = 8 * 1024 * 1024

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path).resolve()
        self.path_guard = PathGuard(self.project_path)

    def expand(self, text: str) -> tuple[str, list[ImageContent]]:
        references = parse_image_references(text)
        if not references:
            return text, []
        images: list[ImageContent] = []
        notes: list[str] = []
        for reference in references[: self.MAX_IMAGES]:
            try:
                image = self._load(reference.path)
                images.append(image)
                notes.append(f"- {reference.path} ({image.mime_type}, {len(image.data)} base64 chars)")
            except Exception as exc:
                notes.append(f"- {reference.path} (error: {exc})")
        if len(references) > self.MAX_IMAGES:
            notes.append(f"- skipped {len(references) - self.MAX_IMAGES} image(s): limit is {self.MAX_IMAGES}")
        block = "<images>\n" + "\n".join(notes) + "\n</images>"
        return block + "\n\n" + text, images

    def _load(self, user_path: str) -> ImageContent:
        safe = self.path_guard.resolve_safe(user_path)
        if not safe.exists() or not safe.is_file():
            raise FileNotFoundError(user_path)
        size = safe.stat().st_size
        if size > self.MAX_IMAGE_BYTES:
            raise PolicyError(f"image exceeds {self.MAX_IMAGE_BYTES} bytes")
        mime_type = _detect_mime_type(safe)
        if mime_type not in SUPPORTED_MIME_TYPES:
            raise PolicyError(f"unsupported image type: {mime_type}")
        return ImageContent(mime_type, base64.b64encode(safe.read_bytes()).decode("ascii"))


def _detect_mime_type(path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"
