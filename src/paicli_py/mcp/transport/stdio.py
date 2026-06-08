from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from ..jsonrpc import FailureHandler, NotificationHandler, RequestHandler


class StdioTransport:
    def __init__(self, command: list[str], cwd: str | Path | None = None):
        if not command:
            raise ValueError("stdio command cannot be empty")
        self.notification_handler: NotificationHandler | None = None
        self.failure_handler: FailureHandler | None = None
        self.request_handler: RequestHandler | None = None
        self._condition = threading.Condition()
        self._responses: dict[Any, dict[str, Any]] = {}
        self._read_error: BaseException | None = None
        self._closed = False
        self.process = subprocess.Popen(
            command,
            cwd=Path(cwd).resolve() if cwd else None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self._reader = threading.Thread(target=self._read_loop, name="paicli-mcp-stdio-reader", daemon=True)
        self._reader.start()

    def set_notification_handler(self, handler: NotificationHandler | None) -> None:
        self.notification_handler = handler

    def set_failure_handler(self, handler: FailureHandler | None) -> None:
        self.failure_handler = handler

    def set_request_handler(self, handler: RequestHandler | None) -> None:
        self.request_handler = handler

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.process.stdin is None:
            raise RuntimeError("stdio transport is closed")
        request_id = payload.get("id")
        with self._condition:
            if self._closed:
                raise RuntimeError("stdio transport is closed")
            if self._read_error is not None:
                raise RuntimeError(f"stdio transport reader failed: {self._read_error}") from self._read_error
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            while request_id not in self._responses:
                if self._read_error is not None:
                    raise RuntimeError(f"stdio transport reader failed: {self._read_error}") from self._read_error
                if self._closed:
                    raise RuntimeError("stdio transport is closed")
                self._condition.wait()
            return self._responses.pop(request_id)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        if self.process.poll() is None:
            self.process.terminate()
        self._reader.join(timeout=2)
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def _read_loop(self) -> None:
        try:
            if self.process.stdout is None:
                raise RuntimeError("stdio transport is closed")
            while True:
                line = self.process.stdout.readline()
                if line == "":
                    break
                line = line.strip()
                if not line:
                    continue
                message = json.loads(line)
                if "id" in message and isinstance(message.get("method"), str):
                    self._dispatch_request(message)
                    continue
                if "id" in message:
                    with self._condition:
                        self._responses[message.get("id")] = message
                        self._condition.notify_all()
                    continue
                self._dispatch_notification(message)
            with self._condition:
                if not self._closed:
                    self._record_read_error(RuntimeError("mcp server closed stdout"))
                    self._condition.notify_all()
        except BaseException as exc:
            with self._condition:
                self._record_read_error(exc)
                self._condition.notify_all()

    def _record_read_error(self, exc: BaseException) -> None:
        self._read_error = exc
        if self.failure_handler is not None and not self._closed:
            threading.Thread(
                target=self.failure_handler,
                args=(exc,),
                name="paicli-mcp-stdio-failure",
                daemon=True,
            ).start()

    def _dispatch_notification(self, message: dict[str, Any]) -> None:
        if self.notification_handler is None or "id" in message:
            return
        if isinstance(message.get("method"), str):
            threading.Thread(
                target=self.notification_handler,
                args=(message,),
                name="paicli-mcp-stdio-notification",
                daemon=True,
            ).start()

    def _dispatch_request(self, message: dict[str, Any]) -> None:
        threading.Thread(
            target=self._handle_request,
            args=(message,),
            name="paicli-mcp-stdio-request",
            daemon=True,
        ).start()

    def _handle_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if self.request_handler is None:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "request handler not configured"},
            }
        else:
            try:
                response = {"jsonrpc": "2.0", "id": request_id, "result": self.request_handler(message)}
            except Exception as exc:
                response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}
        self._write_message(response)

    def _write_message(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None:
            return
        with self._condition:
            if self._closed:
                return
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
