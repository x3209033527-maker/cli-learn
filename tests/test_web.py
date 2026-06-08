import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from paicli_py.tool import ToolInvocation, ToolRegistry
from paicli_py.web import (
    FetchResult,
    SearchProviderConfig,
    SearchResult,
    SerpApiSearchProvider,
    WebFetcher,
    ZhipuSearchProvider,
    create_search_provider,
    pick_search_provider,
)
from paicli_py.web.html import extract_text
from paicli_py.web.policy import NetworkPolicy, NetworkPolicyError


class AllowAllPolicy:
    def validate_url(self, url):
        return None


class FakeFetcher:
    def fetch(self, url):
        return FetchResult(url, "Example", "Hello world", "text/html")


class FakeSearchProvider:
    def search(self, query, limit=5):
        return [SearchResult("Title", "https://example.com", f"snippet {query}")][:limit]


class SearchHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, format, *args):
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/plain":
            self._send_bytes(b"abcdefghijklmnopqrstuvwxyz", "text/plain")
            return
        if parsed.path == "/large":
            self._send_json({"organic_results": [{"title": "x" * 200, "link": "https://example.com"}]})
            return
        SearchHandler.requests.append(
            {"method": "GET", "path": parsed.path, "query": parse_qs(parsed.query)}
        )
        self._send_json(
            {
                "organic_results": [
                    {
                        "title": "Serp result",
                        "link": "https://example.com/serp",
                        "snippet": "Serp snippet",
                    }
                ]
            }
        )

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        SearchHandler.requests.append(
            {
                "method": "POST",
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "json": json.loads(body),
            }
        )
        self._send_json(
            {
                "search_result": [
                    {
                        "title": "Zhipu result",
                        "link": "https://example.com/zhipu",
                        "content": "Zhipu snippet",
                    }
                ]
            }
        )

    def _send_json(self, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_bytes(self, raw, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class LocalSearchServer:
    def __enter__(self):
        SearchHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), SearchHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}"
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()


class WebTest(unittest.TestCase):
    def test_extract_text_removes_script_and_tags(self):
        title, text = extract_text("<html><title>T</title><script>x()</script><body><h1>Hello</h1></body></html>")
        self.assertEqual("T", title)
        self.assertIn("Hello", text)
        self.assertNotIn("x()", text)

    def test_extract_text_prefers_article_and_removes_noise(self):
        title, text = extract_text(
            """
            <html><title>Article title</title><body>
              <nav>Home Products Pricing</nav>
              <div class="sidebar">related related related</div>
              <article>
                <h1>Main headline</h1>
                <p>This is the primary article body with enough text to make the semantic
                container win over surrounding navigation and related content.</p>
              </article>
            </body></html>
            """
        )
        self.assertEqual("Article title", title)
        self.assertIn("# Main headline", text)
        self.assertIn("primary article body", text)
        self.assertNotIn("Pricing", text)
        self.assertNotIn("related related related", text)

    def test_extract_text_renders_common_markdown_blocks(self):
        _, text = extract_text(
            """
            <main>
              <p>Read <a href="/docs">docs</a> with <strong>care</strong> and <code>paicli</code>.</p>
              <ul><li>First</li><li>Second</li></ul>
              <blockquote><p>Quoted line</p></blockquote>
              <pre>mvn test</pre>
              <table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>1</td></tr></table>
            </main>
            """,
            base_url="https://example.com/base/",
        )
        self.assertIn("[docs](https://example.com/docs)", text)
        self.assertIn("**care**", text)
        self.assertIn("`paicli`", text)
        self.assertIn("- First", text)
        self.assertIn("> Quoted line", text)
        self.assertIn("```\nmvn test\n```", text)
        self.assertIn("| Name | Value |", text)

    def test_network_policy_blocks_localhost(self):
        with self.assertRaises(NetworkPolicyError):
            NetworkPolicy().validate_url("http://localhost:8080")

    def test_network_policy_rate_limits_window(self):
        clock = [100.0]
        policy = NetworkPolicy(window_seconds=60, max_requests=2, clock=lambda: clock[0])
        policy.acquire()
        policy.acquire()
        with self.assertRaises(NetworkPolicyError):
            policy.acquire()
        clock[0] = 161.0
        policy.acquire()

    def test_web_fetcher_truncates_and_reports_content_length(self):
        with LocalSearchServer() as server:
            fetcher = WebFetcher(policy=AllowAllPolicy(), max_bytes=5)
            result = fetcher.fetch(f"{server.url}/plain")
        self.assertTrue(result.truncated)
        self.assertEqual(5, result.content_length)
        self.assertEqual("abcde", result.text)
        self.assertIn("Truncated: true", result.format())

    def test_web_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(tmp, web_fetcher=FakeFetcher(), search_provider=FakeSearchProvider())
            fetched = registry.execute(ToolInvocation("1", "web_fetch", {"url": "https://example.com"}))
            self.assertIn("Hello world", fetched.result)
            searched = registry.execute(ToolInvocation("2", "web_search", {"query": "pai cli"}))
            self.assertIn("https://example.com", searched.result)
            self.assertIn("pai cli", searched.result)

    def test_pick_search_provider_matches_java_order(self):
        self.assertEqual("zhipu", pick_search_provider("zhipu", None, "key", "http://searx"))
        self.assertEqual("searxng", pick_search_provider("searxng", "glm", "key", "http://searx"))
        self.assertEqual("serpapi", pick_search_provider("serpapi", None, None, "http://searx"))
        self.assertEqual("zhipu", pick_search_provider("", "glm", "key", "http://searx"))
        self.assertEqual("serpapi", pick_search_provider("", "", "key", "http://searx"))
        self.assertEqual("searxng", pick_search_provider("", "", "", "http://searx"))
        self.assertEqual("zhipu", pick_search_provider("", "", "", ""))

    def test_search_config_reads_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text(
                "\n".join(
                    [
                        "PAICLI_SEARCH_PROVIDER=serpapi",
                        "SERPAPI_API_KEY=serp-key",
                        "PAICLI_SEARCH_TIMEOUT=7",
                    ]
                ),
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {}, clear=True):
                config = SearchProviderConfig.from_env(tmp)
        self.assertEqual("serpapi", config.provider)
        self.assertEqual("serp-key", config.serpapi_key)
        self.assertEqual(7, config.timeout)

    def test_serpapi_provider_requests_and_parses_results(self):
        with LocalSearchServer() as server:
            provider = SerpApiSearchProvider(
                "serp-key",
                f"{server.url}/search.json",
                policy=AllowAllPolicy(),
            )
            results = provider.search("pai cli", 3)
        self.assertEqual("Serp result", results[0].title)
        self.assertEqual("https://example.com/serp", results[0].url)
        self.assertEqual("GET", SearchHandler.requests[0]["method"])
        self.assertEqual(["pai cli"], SearchHandler.requests[0]["query"]["q"])
        self.assertEqual(["serp-key"], SearchHandler.requests[0]["query"]["api_key"])

    def test_search_provider_rejects_oversized_json_response(self):
        with LocalSearchServer() as server:
            provider = SerpApiSearchProvider(
                "serp-key",
                f"{server.url}/large",
                policy=AllowAllPolicy(),
                max_response_bytes=32,
            )
            with self.assertRaises(ValueError):
                provider.search("pai cli")

    def test_zhipu_provider_requests_and_parses_results(self):
        with LocalSearchServer() as server:
            provider = ZhipuSearchProvider(
                "glm-key",
                f"{server.url}/web_search",
                "search_std",
                policy=AllowAllPolicy(),
            )
            results = provider.search("pai cli", 2)
        self.assertEqual("Zhipu result", results[0].title)
        self.assertEqual("https://example.com/zhipu", results[0].url)
        request = SearchHandler.requests[0]
        self.assertEqual("POST", request["method"])
        self.assertEqual("Bearer glm-key", request["authorization"])
        self.assertEqual("pai cli", request["json"]["search_query"])
        self.assertEqual(2, request["json"]["count"])

    def test_factory_defaults_to_zhipu_without_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            provider = create_search_provider(project_path=None)
        self.assertIsInstance(provider, ZhipuSearchProvider)
        self.assertEqual([], provider.search("pai cli"))


if __name__ == "__main__":
    unittest.main()
