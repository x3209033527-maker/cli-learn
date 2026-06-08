import tempfile
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from paicli_py.rag import CodeAnalyzer, CodeChunker, CodeRetriever, EmbeddingConfig, OpenAICompatibleEmbeddingClient, create_embedding_client


SAMPLE_JAVA = """
package demo;

import com.example.UserRepository;
import java.util.List;

class BaseService {}
interface UserApi {}

public class SampleService extends BaseService implements UserApi {
    public String greet(String name) {
        audit(name);
        return "hello " + name;
    }

    private int add(int left, int right) {
        return left + right;
    }
}
""".strip()


class RagTest(unittest.TestCase):
    def test_java_chunker_extracts_class_and_methods(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "SampleService.java"
            file_path.write_text(SAMPLE_JAVA, encoding="utf-8")
            chunks = CodeChunker().chunk_file(root, file_path)
            names = {chunk.name for chunk in chunks}
            self.assertIn("SampleService", names)
            self.assertIn("SampleService.greet", names)
            self.assertIn("SampleService.add", names)

    def test_java_chunker_handles_annotations_multiline_signatures_and_constructors(self):
        source = """
package demo;

public class AdvancedService {
    @Inject
    public AdvancedService(
            Dependency dependency
    ) {
        this.dependency = dependency;
    }

    @Override
    public String render(
            String name,
            int count
    ) throws IOException {
        String literal = "{ not a block }";
        // } not a block either
        return name + count;
    }
}
""".strip()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "AdvancedService.java"
            file_path.write_text(source, encoding="utf-8")
            chunks = CodeChunker().chunk_file(root, file_path)
            by_name = {chunk.name: chunk for chunk in chunks}

            self.assertIn("AdvancedService", by_name)
            self.assertIn("AdvancedService.AdvancedService", by_name)
            self.assertIn("AdvancedService.render", by_name)
            self.assertIn("@Override", by_name["AdvancedService.render"].content)
            self.assertIn('"{ not a block }"', by_name["AdvancedService.render"].content)

    def test_java_analyzer_extracts_relations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "SampleService.java"
            file_path.write_text(SAMPLE_JAVA, encoding="utf-8")
            relations = CodeAnalyzer().analyze_file(root, file_path)
            keys = {(relation.from_name, relation.to_name, relation.relation_type) for relation in relations}

            self.assertIn(("file", "UserRepository", "imports"), keys)
            self.assertNotIn(("file", "List", "imports"), keys)
            self.assertIn(("SampleService", "BaseService", "extends"), keys)
            self.assertIn(("SampleService", "UserApi", "implements"), keys)
            self.assertIn(("SampleService", "SampleService.greet", "contains"), keys)
            self.assertIn(("SampleService", "audit", "calls"), keys)

    def test_retriever_indexes_and_searches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "SampleService.java").write_text(SAMPLE_JAVA, encoding="utf-8")
            retriever = CodeRetriever(root)
            self.assertGreaterEqual(retriever.index(), 3)
            results = retriever.search("greet name", top_k=3)
            self.assertTrue(any("greet" in result.name for result in results))
            self.assertIn("SampleService", retriever.format_results(results))
            relations = retriever.relations_for("SampleService")
            self.assertTrue(any(relation.relation_type == "extends" for relation in relations))

    def test_retriever_includes_relation_context_in_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "SampleService.java").write_text(SAMPLE_JAVA, encoding="utf-8")
            retriever = CodeRetriever(root)
            retriever.index()

            results = retriever.search("BaseService extends", top_k=5)
            formatted = retriever.format_results(results)

            self.assertTrue(any(result.relations for result in results))
            self.assertIn("relations:", formatted)
            self.assertIn("extends:SampleService->BaseService", formatted)

    def test_embedding_config_defaults_to_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {}, clear=True):
                config = EmbeddingConfig.from_env(Path(tmp))
            client = create_embedding_client(config)
            vector = client.embed("hello world")
            self.assertEqual(128, len(vector))

    def test_embedding_config_reads_project_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "\n".join([
                    "PAICLI_EMBEDDING_PROVIDER=openai",
                    "PAICLI_EMBEDDING_API_KEY=test-key",
                    "PAICLI_EMBEDDING_API_URL=http://127.0.0.1/embeddings",
                    "PAICLI_EMBEDDING_MODEL=embed-test",
                ]),
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {}, clear=True):
                config = EmbeddingConfig.from_env(root)
            self.assertEqual("openai", config.provider)
            self.assertEqual("test-key", config.api_key)
            self.assertEqual("embed-test", config.model)

    def test_openai_compatible_embedding_client(self):
        class Handler(BaseHTTPRequestHandler):
            payload = None

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.__class__.payload = json.loads(self.rfile.read(length).decode("utf-8"))
                body = json.dumps({"data": [{"embedding": [0.1, 0.2, 0.3]}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        host, port = httpd.server_address
        try:
            client = OpenAICompatibleEmbeddingClient(f"http://{host}:{port}/embeddings", "key", "embed-test")
            self.assertEqual([0.1, 0.2, 0.3], client.embed("hello"))
            self.assertEqual("embed-test", Handler.payload["model"])
            self.assertEqual("hello", Handler.payload["input"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
