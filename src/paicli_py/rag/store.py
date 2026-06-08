from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .analyzer import CodeRelation
from .chunker import CodeChunk
from .embedding import cosine_similarity
from .retriever_types import SearchResult


class VectorStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self._init_tables()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "VectorStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def clear_project(self, project_path: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM code_chunks WHERE project_path = ?", (project_path,))
            self.connection.execute("DELETE FROM code_relations WHERE project_path = ?", (project_path,))

    def insert_chunks(self, project_path: str, entries: list[tuple[CodeChunk, list[float]]]) -> None:
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO code_chunks
                (project_path, file_path, chunk_type, name, content, start_line, end_line, embedding_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        project_path,
                        chunk.file_path,
                        chunk.chunk_type,
                        chunk.name,
                        chunk.content,
                        chunk.start_line,
                        chunk.end_line,
                        json.dumps(vector),
                    )
                    for chunk, vector in entries
                ],
            )

    def insert_relations(self, project_path: str, relations: list[CodeRelation]) -> None:
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO code_relations
                (project_path, from_file, from_name, to_file, to_name, relation_type)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        project_path,
                        relation.from_file,
                        relation.from_name,
                        relation.to_file,
                        relation.to_name,
                        relation.relation_type,
                    )
                    for relation in relations
                ],
            )

    def relations_for(self, project_path: str, name: str) -> list[CodeRelation]:
        rows = self.connection.execute(
            """
            SELECT from_file, from_name, to_file, to_name, relation_type
            FROM code_relations
            WHERE project_path = ? AND (from_name = ? OR to_name = ?)
            ORDER BY relation_type, from_name, to_name
            """,
            (project_path, name, name),
        ).fetchall()
        return [CodeRelation(row[0], row[1], row[2], row[3], row[4]) for row in rows]

    def search(self, project_path: str, query_embedding: list[float], top_k: int = 5) -> list[SearchResult]:
        rows = self.connection.execute(
            """
            SELECT file_path, chunk_type, name, content, start_line, end_line, embedding_json
            FROM code_chunks
            WHERE project_path = ?
            """,
            (project_path,),
        ).fetchall()
        results = []
        for row in rows:
            embedding = json.loads(row[6])
            results.append(SearchResult(
                file_path=row[0],
                chunk_type=row[1],
                name=row[2],
                content=row[3],
                start_line=row[4],
                end_line=row[5],
                score=cosine_similarity(query_embedding, embedding),
                relations=self.relations_for(project_path, row[2]),
            ))
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    def search_keyword(self, project_path: str, keyword: str, top_k: int = 5) -> list[SearchResult]:
        like = f"%{keyword}%"
        rows = self.connection.execute(
            """
            SELECT file_path, chunk_type, name, content, start_line, end_line
            FROM code_chunks
            WHERE project_path = ? AND (name LIKE ? OR content LIKE ?)
            LIMIT ?
            """,
            (project_path, like, like, top_k),
        ).fetchall()
        return [
            SearchResult(row[0], row[1], row[2], row[3], row[4], row[5], 0.5, self.relations_for(project_path, row[2]))
            for row in rows
        ]

    def count_chunks(self, project_path: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM code_chunks WHERE project_path = ?",
            (project_path,),
        ).fetchone()
        return int(row[0])

    def count_relations(self, project_path: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM code_relations WHERE project_path = ?",
            (project_path,),
        ).fetchone()
        return int(row[0])

    def _init_tables(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS code_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_path TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    chunk_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_code_chunks_project ON code_chunks(project_path)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_code_chunks_name ON code_chunks(name)")
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS code_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_path TEXT NOT NULL,
                    from_file TEXT NOT NULL,
                    from_name TEXT NOT NULL,
                    to_file TEXT,
                    to_name TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_code_rel_project ON code_relations(project_path)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_code_rel_from ON code_relations(from_name)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_code_rel_to ON code_relations(to_name)")
