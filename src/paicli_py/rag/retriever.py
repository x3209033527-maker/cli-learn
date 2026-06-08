from __future__ import annotations

from pathlib import Path

from .analyzer import CodeAnalyzer, CodeRelation
from .chunker import CodeChunker
from .embedding import EmbeddingConfig, EmbeddingProvider, create_embedding_client
from .retriever_types import SearchResult
from .store import VectorStore


class CodeRetriever:
    INDEXABLE_SUFFIXES = {
        ".java", ".py", ".js", ".ts", ".go", ".rs", ".c", ".cpp", ".h",
        ".md", ".xml", ".properties", ".yaml", ".yml", ".json", ".sh",
        ".gradle", ".kt",
    }
    SKIP_DIRS = {"node_modules", "target", "build", ".git", ".idea", ".vscode", "dist", "out", "__pycache__"}

    def __init__(
        self,
        project_path: str | Path,
        db_path: str | Path | None = None,
        embedding_client: EmbeddingProvider | None = None,
    ):
        self.project_path = Path(project_path).resolve()
        self.db_path = Path(db_path) if db_path else self.project_path / ".paicli-py" / "rag.sqlite"
        self.embedding = embedding_client or create_embedding_client(EmbeddingConfig.from_env(self.project_path))
        self.chunker = CodeChunker()
        self.analyzer = CodeAnalyzer()

    def index(self) -> int:
        entries = []
        relations: list[CodeRelation] = []
        for file_path in self._iter_files():
            for chunk in self.chunker.chunk_file(self.project_path, file_path):
                entries.append((chunk, self.embedding.embed(chunk.embedding_text())))
            relations.extend(self.analyzer.analyze_file(self.project_path, file_path))
        with VectorStore(self.db_path) as store:
            store.clear_project(str(self.project_path))
            store.insert_chunks(str(self.project_path), entries)
            store.insert_relations(str(self.project_path), relations)
            return store.count_chunks(str(self.project_path))

    def relations_for(self, name: str) -> list[CodeRelation]:
        with VectorStore(self.db_path) as store:
            return store.relations_for(str(self.project_path), name)

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        with VectorStore(self.db_path) as store:
            keyword_hits = store.search_keyword(str(self.project_path), query, top_k)
            semantic_hits = store.search(str(self.project_path), self.embedding.embed(query), top_k)
        merged: dict[tuple[str, str, int], SearchResult] = {}
        for result in keyword_hits + semantic_hits:
            result = self._boost_with_relations(result, query)
            key = (result.file_path, result.name, result.start_line)
            current = merged.get(key)
            if current is None or result.score > current.score:
                merged[key] = result
        return sorted(merged.values(), key=lambda item: item.score, reverse=True)[:top_k]

    def format_results(self, results: list[SearchResult]) -> str:
        if not results:
            return "No indexed code results. Run index_code first."
        lines = []
        for index, result in enumerate(results, start=1):
            preview = result.content.replace("\n", " ")
            if len(preview) > 180:
                preview = preview[:177] + "..."
            lines.append(
                f"{index}. {result.file_path}:{result.start_line}-{result.end_line} "
                f"[{result.chunk_type}] {result.name} score={result.score:.3f}\n"
                f"   {preview}"
            )
            if result.relations:
                relation_text = ", ".join(
                    f"{relation.relation_type}:{relation.from_name}->{relation.to_name}"
                    for relation in result.relations[:4]
                )
                lines.append(f"   relations: {relation_text}")
        return "\n".join(lines)

    def _boost_with_relations(self, result: SearchResult, query: str) -> SearchResult:
        if not result.relations:
            return result
        query_lower = query.lower()
        boost = 0.0
        for relation in result.relations:
            haystack = f"{relation.relation_type} {relation.from_name} {relation.to_name}".lower()
            if any(token and token in haystack for token in query_lower.split()):
                boost += 0.08
        if boost <= 0:
            return result
        return SearchResult(
            result.file_path,
            result.chunk_type,
            result.name,
            result.content,
            result.start_line,
            result.end_line,
            result.score + min(boost, 0.24),
            result.relations,
        )

    def _iter_files(self):
        for path in self.project_path.rglob("*"):
            if not path.is_file():
                continue
            parts = set(path.parts)
            if parts & self.SKIP_DIRS:
                continue
            if path.suffix in self.INDEXABLE_SUFFIXES:
                yield path
