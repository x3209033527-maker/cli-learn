from __future__ import annotations

from dataclasses import dataclass
from urllib.request import Request, urlopen

from .html import extract_text
from .limits import read_bounded
from .policy import NetworkPolicy


@dataclass(frozen=True)
class FetchResult:
    url: str
    title: str
    text: str
    content_type: str
    truncated: bool = False
    content_length: int = 0
    body_empty: bool = False
    hint: str = ""

    def format(self) -> str:
        title = f"# {self.title}\n\n" if self.title else ""
        truncated = "\nTruncated: true" if self.truncated else ""
        hint = f"\nHint: {self.hint}" if self.hint else ""
        return (
            f"{title}URL: {self.url}\nContent-Type: {self.content_type}\n"
            f"Content-Length: {self.content_length}{truncated}{hint}\n\n{self.text}"
        )


class WebFetcher:
    def __init__(self, policy: NetworkPolicy | None = None, timeout: int = 30, max_bytes: int = 5 * 1024 * 1024):
        self.policy = policy or NetworkPolicy()
        self.timeout = timeout
        self.max_bytes = max_bytes

    def fetch(self, url: str) -> FetchResult:
        self.policy.validate_url(url)
        _acquire(self.policy)
        request = Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                "User-Agent": "paicli-py-web-fetch/0.1",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            raw, truncated = read_bounded(response, self.max_bytes)
            content_type = response.headers.get("Content-Type", "")
        charset = _charset_from_content_type(content_type)
        text = raw.decode(charset, errors="replace")
        if "html" in content_type.lower() or "<html" in text[:200].lower():
            title, extracted = extract_text(text)
        else:
            title, extracted = "", text
        body_empty = not extracted.strip()
        hint = (
            "No readable body extracted. The page may require JavaScript rendering or block crawlers."
            if body_empty
            else ""
        )
        return FetchResult(url, title, extracted, content_type, truncated, len(raw), body_empty, hint)


def _charset_from_content_type(content_type: str) -> str:
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1]
    return "utf-8"


def _acquire(policy: NetworkPolicy) -> None:
    acquire = getattr(policy, "acquire", None)
    if callable(acquire):
        acquire()
