from __future__ import annotations

import inspect
import json
from collections.abc import Iterable
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class RuntimeEvent:
    id: int
    thread_id: str
    type: str
    data: str
    created_at: str


@dataclass(frozen=True)
class RuntimeTurn:
    id: str
    thread_id: str
    status: str
    input: str
    result: str = ""
    error: str = ""
    created_at: str = ""
    finished_at: str = ""


class CancellationToken:
    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def canceled(self) -> bool:
        return self._event.is_set()

    def throw_if_canceled(self) -> None:
        if self.canceled:
            raise RuntimeError("turn canceled")


class RuntimeThreadStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_tables()

    def create_thread(self) -> str:
        thread_id = "thread_" + uuid.uuid4().hex[:12]
        with self._lock:
            self.connection.execute("INSERT INTO runtime_threads (id, created_at) VALUES (?, ?)", (thread_id, _now()))
            self.connection.commit()
            self.append_event(thread_id, "thread.created", json.dumps({"thread_id": thread_id}))
        return thread_id

    def exists(self, thread_id: str) -> bool:
        with self._lock:
            row = self.connection.execute("SELECT 1 FROM runtime_threads WHERE id = ?", (thread_id,)).fetchone()
            return row is not None

    def create_turn(self, thread_id: str, user_input: str) -> RuntimeTurn:
        turn_id = "turn_" + uuid.uuid4().hex[:12]
        now = _now()
        with self._lock:
            self.connection.execute(
                "INSERT INTO runtime_turns (id, thread_id, status, input, created_at) VALUES (?, ?, ?, ?, ?)",
                (turn_id, thread_id, "running", user_input, now),
            )
            self.connection.commit()
        return self.find_turn(turn_id)  # type: ignore[return-value]

    def find_turn(self, turn_id: str) -> RuntimeTurn | None:
        with self._lock:
            row = self.connection.execute("SELECT * FROM runtime_turns WHERE id = ?", (turn_id,)).fetchone()
            return self._turn_from_row(row) if row else None

    def turn_status(self, turn_id: str) -> str:
        turn = self.find_turn(turn_id)
        return turn.status if turn else "missing"

    def mark_turn_terminal(self, turn_id: str, status: str, result: str = "", error: str = "") -> None:
        with self._lock:
            self.connection.execute(
                "UPDATE runtime_turns SET status = ?, result = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, result or "", error or "", _now(), turn_id),
            )
            self.connection.commit()

    def cancel_turn(self, thread_id: str, turn_id: str) -> bool:
        with self._lock:
            turn = self.find_turn(turn_id)
            if turn is None or turn.thread_id != thread_id or turn.status != "running":
                return False
            self.mark_turn_terminal(turn_id, "canceling", error="cancel requested")
            self.append_event(thread_id, "turn.canceling", json.dumps({"turn_id": turn_id, "status": "canceling"}))
            return True

    def append_event(self, thread_id: str, event_type: str, data: str) -> int:
        with self._lock:
            cursor = self.connection.execute(
                "INSERT INTO runtime_events (thread_id, type, data, created_at) VALUES (?, ?, ?, ?)",
                (thread_id, event_type, data or "{}", _now()),
            )
            self.connection.commit()
            return int(cursor.lastrowid)

    def events(self, thread_id: str, after_id: int = 0) -> list[RuntimeEvent]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM runtime_events WHERE thread_id = ? AND id > ? ORDER BY id ASC",
                (thread_id, after_id),
            ).fetchall()
            return [RuntimeEvent(int(row["id"]), row["thread_id"], row["type"], row["data"], row["created_at"]) for row in rows]

    def close(self) -> None:
        self.connection.close()

    def _init_tables(self) -> None:
        self.connection.execute("CREATE TABLE IF NOT EXISTS runtime_threads (id TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_turns (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                status TEXT NOT NULL,
                input TEXT NOT NULL,
                result TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                finished_at TEXT DEFAULT ''
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                type TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_runtime_events_thread ON runtime_events(thread_id, id)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_runtime_turns_thread ON runtime_turns(thread_id, id)")
        self.connection.commit()

    def _turn_from_row(self, row: sqlite3.Row) -> RuntimeTurn:
        return RuntimeTurn(row["id"], row["thread_id"], row["status"], row["input"], row["result"] or "", row["error"] or "", row["created_at"] or "", row["finished_at"] or "")


class RuntimeApiServer:
    def __init__(self, store: RuntimeThreadStore, runner, api_key: str, host: str = "127.0.0.1", port: int = 0):
        if not api_key:
            raise ValueError("Runtime API requires an API key")
        self.store = store
        self.runner = runner
        self.api_key = api_key
        self._tokens: dict[str, CancellationToken] = {}
        self._tokens_lock = threading.RLock()
        handler = self._handler_class()
        self.server = ThreadingHTTPServer((host, port), handler)
        self.server.runtime_api = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self.server.serve_forever, name="paicli-runtime-api", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.server.server_close()

    def _handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return None

            def do_POST(self):
                api = self.server.runtime_api  # type: ignore[attr-defined]
                if not api._authorized(self):
                    self._json(401, {"error": "unauthorized"})
                    return
                path = urlparse(self.path).path
                if path == "/v1/threads":
                    thread_id = api.store.create_thread()
                    self._json(200, {"id": thread_id, "object": "thread"})
                    return
                if path.startswith("/v1/threads/") and path.endswith("/turns"):
                    api._handle_turn(self, path.split("/")[3])
                    return
                if path.startswith("/v1/threads/") and path.endswith("/cancel"):
                    parts = path.split("/")
                    api._handle_cancel(self, parts[3], parts[5])
                    return
                self._json(404, {"error": "not_found"})

            def do_GET(self):
                api = self.server.runtime_api  # type: ignore[attr-defined]
                if not api._authorized(self):
                    self._json(401, {"error": "unauthorized"})
                    return
                parsed = urlparse(self.path)
                if parsed.path.startswith("/v1/threads/") and parsed.path.endswith("/events"):
                    api._handle_events(self, parsed.path.split("/")[3], parsed.query)
                    return
                self._json(404, {"error": "not_found"})

            def _json(self, status: int, payload: dict):
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        return Handler

    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        auth = handler.headers.get("Authorization", "")
        direct = handler.headers.get("X-PaiCLI-API-Key", "")
        return auth == f"Bearer {self.api_key}" or direct == self.api_key

    def _handle_turn(self, handler: BaseHTTPRequestHandler, thread_id: str) -> None:
        if not self.store.exists(thread_id):
            handler._json(404, {"error": "thread_not_found"})  # type: ignore[attr-defined]
            return
        length = int(handler.headers.get("Content-Length", "0") or "0")
        payload = json.loads(handler.rfile.read(length).decode("utf-8") or "{}")
        user_input = str(payload.get("input", "")).strip()
        if not user_input:
            handler._json(400, {"error": "input_required"})  # type: ignore[attr-defined]
            return
        turn = self.store.create_turn(thread_id, user_input)
        token = CancellationToken()
        with self._tokens_lock:
            self._tokens[turn.id] = token
        self.store.append_event(thread_id, "turn.started", json.dumps({"turn_id": turn.id, "input": user_input}))
        threading.Thread(target=self._run_turn, args=(thread_id, turn.id, user_input, token), daemon=True).start()
        handler._json(202, {"id": turn.id, "object": "turn", "status": "running"})  # type: ignore[attr-defined]

    def _handle_cancel(self, handler: BaseHTTPRequestHandler, thread_id: str, turn_id: str) -> None:
        if not self.store.exists(thread_id):
            handler._json(404, {"error": "thread_not_found"})  # type: ignore[attr-defined]
            return
        canceled = self.store.cancel_turn(thread_id, turn_id)
        if canceled:
            with self._tokens_lock:
                token = self._tokens.get(turn_id)
                if token is not None:
                    token.cancel()
            handler._json(202, {"id": turn_id, "status": "canceling"})  # type: ignore[attr-defined]
            return
        handler._json(404, {"error": "turn_not_running"})  # type: ignore[attr-defined]

    def _run_turn(self, thread_id: str, turn_id: str, user_input: str, token: CancellationToken) -> None:
        chunks: list[str] = []

        def emit(chunk) -> None:
            token.throw_if_canceled()
            if self.store.turn_status(turn_id) == "canceling":
                token.cancel()
                token.throw_if_canceled()
            text = "" if chunk is None else str(chunk)
            if not text:
                return
            chunks.append(text)
            self.store.append_event(thread_id, "message.delta", json.dumps({"turn_id": turn_id, "content": text}))

        try:
            result = _run_with_optional_token(self.runner, user_input, token, emit)
            if _is_stream_result(result):
                for chunk in result:
                    emit(chunk)
                result_text = "".join(chunks)
            else:
                result_text = "" if result is None else str(result)
                if not chunks and result_text:
                    emit(result_text)
                elif result_text and "".join(chunks) != result_text:
                    chunks.append(result_text)
            if token.canceled or self.store.turn_status(turn_id) == "canceling":
                self.store.mark_turn_terminal(turn_id, "canceled", result="".join(chunks), error="turn canceled")
                self.store.append_event(thread_id, "turn.canceled", json.dumps({"turn_id": turn_id, "status": "canceled"}))
                return
            final_result = "".join(chunks) if chunks else result_text
            self.store.mark_turn_terminal(turn_id, "completed", result=final_result)
            self.store.append_event(thread_id, "turn.completed", json.dumps({"turn_id": turn_id, "status": "completed"}))
        except Exception as exc:
            if token.canceled or self.store.turn_status(turn_id) == "canceling":
                self.store.mark_turn_terminal(turn_id, "canceled", result="".join(chunks), error="turn canceled")
                self.store.append_event(thread_id, "turn.canceled", json.dumps({"turn_id": turn_id, "status": "canceled"}))
            else:
                self.store.mark_turn_terminal(turn_id, "failed", result="".join(chunks), error=str(exc))
                self.store.append_event(thread_id, "turn.failed", json.dumps({"turn_id": turn_id, "error": str(exc)}))
        finally:
            with self._tokens_lock:
                self._tokens.pop(turn_id, None)

    def _handle_events(self, handler: BaseHTTPRequestHandler, thread_id: str, query: str) -> None:
        if not self.store.exists(thread_id):
            handler._json(404, {"error": "thread_not_found"})  # type: ignore[attr-defined]
            return
        after = int(parse_qs(query).get("after", ["0"])[0] or 0)
        raw = format_sse(self.store.events(thread_id, after)).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)


def format_sse(events: list[RuntimeEvent]) -> str:
    chunks = []
    for event in events:
        chunks.append(f"id: {event.id}\nevent: {event.type}\ndata: {event.data}\n")
    return "\n".join(chunks) + ("\n" if chunks else "")


def _run_with_optional_token(runner, user_input: str, token: CancellationToken, emit=None):
    try:
        parameters = inspect.signature(runner).parameters
        if len(parameters) >= 3:
            return runner(user_input, token, emit)
        if len(parameters) >= 2:
            return runner(user_input, token)
    except (TypeError, ValueError):
        pass
    return runner(user_input)


def _is_stream_result(value) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, dict))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
