from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]:
        ...


class EmbeddingClient:
    """Deterministic local embedding used as a migration scaffold.

    The Java project can call remote or Ollama embeddings. For the Python port
    we keep the contract but start with a dependency-free hashed bag-of-words
    vector so indexing and retrieval are testable offline.
    """

    TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+")

    def __init__(self, dimensions: int = 128):
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in self.TOKEN_PATTERN.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str = "local"
    model: str = "local-hash"
    api_key: str = ""
    api_url: str = ""
    dimensions: int = 128
    timeout: int = 60

    @staticmethod
    def from_env(project_dir: Path | None = None) -> "EmbeddingConfig":
        if project_dir is not None:
            _load_dotenv(project_dir / ".env")
        provider = os.getenv("PAICLI_EMBEDDING_PROVIDER", "local").strip().lower() or "local"
        dimensions = int(os.getenv("PAICLI_EMBEDDING_DIMENSIONS", "128") or "128")
        timeout = int(os.getenv("PAICLI_EMBEDDING_TIMEOUT", "60") or "60")
        if provider in {"openai", "openai-compatible"}:
            return EmbeddingConfig(
                provider="openai",
                model=os.getenv("PAICLI_EMBEDDING_MODEL", "text-embedding-3-small"),
                api_key=os.getenv("PAICLI_EMBEDDING_API_KEY", os.getenv("OPENAI_API_KEY", "")),
                api_url=os.getenv("PAICLI_EMBEDDING_API_URL", "https://api.openai.com/v1/embeddings"),
                dimensions=dimensions,
                timeout=timeout,
            )
        return EmbeddingConfig(provider="local", dimensions=dimensions, timeout=timeout)


class OpenAICompatibleEmbeddingClient:
    def __init__(self, api_url: str, api_key: str, model: str, timeout: int = 60):
        if not api_url:
            raise ValueError("embedding api_url is required")
        if not api_key:
            raise ValueError("embedding api_key is required")
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def embed(self, text: str) -> list[float]:
        payload = {"model": self.model, "input": text}
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
        data = json.loads(raw)
        values = data.get("data", [{}])[0].get("embedding", [])
        return [float(value) for value in values]


def create_embedding_client(config: EmbeddingConfig | None = None) -> EmbeddingProvider:
    config = config or EmbeddingConfig.from_env()
    if config.provider == "openai":
        return OpenAICompatibleEmbeddingClient(config.api_url, config.api_key, config.model, timeout=config.timeout)
    return EmbeddingClient(config.dimensions)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    size = min(len(left), len(right))
    if size == 0:
        return 0.0
    return sum(left[index] * right[index] for index in range(size))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
