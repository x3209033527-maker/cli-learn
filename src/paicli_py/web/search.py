from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .limits import read_bounded
from .policy import NetworkPolicy


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider:
    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        raise NotImplementedError


@dataclass(frozen=True)
class SearchProviderConfig:
    provider: str = "zhipu"
    searxng_url: str = ""
    serpapi_key: str = ""
    serpapi_url: str = "https://serpapi.com/search.json"
    zhipu_key: str = ""
    zhipu_url: str = "https://open.bigmodel.cn/api/paas/v4/web_search"
    zhipu_engine: str = "search_std"
    timeout: int = 20
    max_response_bytes: int = 1024 * 1024

    @classmethod
    def from_env(cls, project_path: str | Path | None = None) -> "SearchProviderConfig":
        values = dict(os.environ)
        if project_path is not None:
            values.update(_read_dotenv(Path(project_path) / ".env"))
        provider = pick_search_provider(
            values.get("PAICLI_SEARCH_PROVIDER", ""),
            values.get("GLM_API_KEY") or values.get("ZHIPU_API_KEY", ""),
            values.get("SERPAPI_API_KEY", ""),
            values.get("SEARXNG_URL", ""),
        )
        return cls(
            provider=provider,
            searxng_url=values.get("SEARXNG_URL", ""),
            serpapi_key=values.get("SERPAPI_API_KEY", ""),
            serpapi_url=values.get("SERPAPI_URL", cls.serpapi_url),
            zhipu_key=values.get("GLM_API_KEY") or values.get("ZHIPU_API_KEY", ""),
            zhipu_url=values.get("ZHIPU_SEARCH_URL", cls.zhipu_url),
            zhipu_engine=values.get("ZHIPU_SEARCH_ENGINE", cls.zhipu_engine),
            timeout=_parse_timeout(values.get("PAICLI_SEARCH_TIMEOUT"), cls.timeout),
            max_response_bytes=_parse_size(values.get("PAICLI_SEARCH_MAX_BYTES"), cls.max_response_bytes),
        )


def pick_search_provider(explicit: str | None, glm_key: str | None, serpapi_key: str | None, searxng_url: str | None) -> str:
    chosen = (explicit or "").strip().lower()
    if chosen:
        return chosen
    if (glm_key or "").strip():
        return "zhipu"
    if (serpapi_key or "").strip():
        return "serpapi"
    if (searxng_url or "").strip():
        return "searxng"
    return "zhipu"


def create_search_provider(
    config: SearchProviderConfig | None = None,
    policy: NetworkPolicy | None = None,
    project_path: str | Path | None = None,
) -> SearchProvider:
    selected = config or SearchProviderConfig.from_env(project_path)
    if selected.provider == "searxng":
        return SearxngSearchProvider(selected.searxng_url, policy, selected.timeout, selected.max_response_bytes)
    if selected.provider == "serpapi":
        return SerpApiSearchProvider(
            selected.serpapi_key,
            selected.serpapi_url,
            policy,
            selected.timeout,
            selected.max_response_bytes,
        )
    return ZhipuSearchProvider(
        selected.zhipu_key,
        selected.zhipu_url,
        selected.zhipu_engine,
        policy,
        selected.timeout,
        selected.max_response_bytes,
    )


class SearxngSearchProvider(SearchProvider):
    def __init__(
        self,
        base_url: str | None = None,
        policy: NetworkPolicy | None = None,
        timeout: int = 20,
        max_response_bytes: int = 1024 * 1024,
    ):
        self.base_url = (base_url or os.getenv("SEARXNG_URL", "")).rstrip("/")
        self.policy = policy or NetworkPolicy()
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        if not self.base_url:
            return []
        url = f"{self.base_url}/search?{urlencode({'q': query, 'format': 'json'})}"
        self.policy.validate_url(url)
        _acquire(self.policy)
        with urlopen(url, timeout=self.timeout) as response:
            payload = _read_json_response(response, self.max_response_bytes, "SearXNG")
        results = []
        for item in payload.get("results", [])[:limit]:
            results.append(SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("content", "")),
            ))
        return results


class SerpApiSearchProvider(SearchProvider):
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://serpapi.com/search.json",
        policy: NetworkPolicy | None = None,
        timeout: int = 20,
        max_response_bytes: int = 1024 * 1024,
    ):
        self.api_key = (api_key or os.getenv("SERPAPI_API_KEY", "")).strip()
        self.base_url = base_url
        self.policy = policy or NetworkPolicy()
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        if not self.api_key:
            return []
        params = urlencode({"engine": "google", "q": query, "api_key": self.api_key, "num": limit})
        separator = "&" if "?" in self.base_url else "?"
        url = f"{self.base_url}{separator}{params}"
        self.policy.validate_url(url)
        _acquire(self.policy)
        with urlopen(url, timeout=self.timeout) as response:
            payload = _read_json_response(response, self.max_response_bytes, "SerpAPI")
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("link") or item.get("url") or ""),
                snippet=str(item.get("snippet") or item.get("description") or ""),
            )
            for item in payload.get("organic_results", [])[:limit]
        ]


class ZhipuSearchProvider(SearchProvider):
    def __init__(
        self,
        api_key: str | None = None,
        api_url: str = "https://open.bigmodel.cn/api/paas/v4/web_search",
        engine: str = "search_std",
        policy: NetworkPolicy | None = None,
        timeout: int = 20,
        max_response_bytes: int = 1024 * 1024,
    ):
        self.api_key = (api_key or os.getenv("GLM_API_KEY") or os.getenv("ZHIPU_API_KEY", "")).strip()
        self.api_url = api_url
        self.engine = engine
        self.policy = policy or NetworkPolicy()
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        if not self.api_key:
            return []
        self.policy.validate_url(self.api_url)
        _acquire(self.policy)
        body = json.dumps(
            {"search_engine": self.engine, "search_query": query, "count": limit}
        ).encode("utf-8")
        request = Request(
            self.api_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            payload = _read_json_response(response, self.max_response_bytes, "Zhipu")
        return _parse_zhipu_results(payload, limit)


def format_search_results(results: list[SearchResult]) -> str:
    if not results:
        return (
            "No search provider configured or no results. Configure PAICLI_SEARCH_PROVIDER "
            "with GLM_API_KEY, SERPAPI_API_KEY, or SEARXNG_URL for web_search."
        )
    return "\n".join(
        f"{index}. {result.title}\n   {result.url}\n   {result.snippet}"
        for index, result in enumerate(results, start=1)
    )


def _parse_zhipu_results(payload: dict, limit: int) -> list[SearchResult]:
    raw = payload.get("search_result") or payload.get("results") or payload.get("data") or []
    if isinstance(raw, dict):
        raw = raw.get("search_result") or raw.get("results") or raw.get("items") or []
    results = []
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        results.append(
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("link") or item.get("url") or ""),
                snippet=str(item.get("content") or item.get("snippet") or item.get("description") or ""),
            )
        )
    return results


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _parse_timeout(raw: str | None, default: int) -> int:
    try:
        return max(1, int(raw)) if raw else default
    except ValueError:
        return default


def _parse_size(raw: str | None, default: int) -> int:
    try:
        return max(1024, int(raw)) if raw else default
    except ValueError:
        return default


def _read_json_response(response, max_bytes: int, provider: str) -> dict:
    raw, truncated = read_bounded(response, max_bytes)
    if truncated:
        raise ValueError(f"{provider} response exceeded {max_bytes} bytes")
    return json.loads(raw.decode("utf-8"))


def _acquire(policy: NetworkPolicy) -> None:
    acquire = getattr(policy, "acquire", None)
    if callable(acquire):
        acquire()
