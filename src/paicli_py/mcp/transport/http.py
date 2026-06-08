from __future__ import annotations

import json
import threading
from typing import Any
from urllib.request import Request, urlopen

from ..jsonrpc import FailureHandler, NotificationHandler, RequestHandler


class StreamableHttpTransport:
    def __init__(self, url: str, timeout: int = 60, headers: dict[str, str] | None = None):
        if not url:
            raise ValueError("streamable HTTP URL cannot be empty")
        self.url = url
        self.timeout = timeout
        self.headers = headers or {}
        self.notification_handler: NotificationHandler | None = None
        self.failure_handler: FailureHandler | None = None
        self.request_handler: RequestHandler | None = None
        self._listener: threading.Thread | None = None
        self._listener_stop = threading.Event()
        self._listener_error: BaseException | None = None

    def set_notification_handler(self, handler: NotificationHandler | None) -> None:
        self.notification_handler = handler

    def set_failure_handler(self, handler: FailureHandler | None) -> None:
        self.failure_handler = handler

    def set_request_handler(self, handler: RequestHandler | None) -> None:
        self.request_handler = handler

    def start_notification_listener(self) -> None:
        if self._listener is not None and self._listener.is_alive():
            return
        self._listener_stop.clear()
        self._listener_error = None
        self._listener = threading.Thread(
            target=self._listen_for_notifications,
            name="paicli-mcp-http-notifications",
            daemon=True,
        )
        self._listener.start()

    def close(self) -> None:
        self._listener_stop.set()
        if self._listener is not None:
            self._listener.join(timeout=2)

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                **self.headers,
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            raw = response.read().decode("utf-8")
        if "text/event-stream" in content_type:
            return _parse_sse_response(raw, payload.get("id"), self.notification_handler, self.request_handler)
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            for item in parsed:
                _dispatch_message(item, self.notification_handler, self.request_handler)
                if item.get("id") == payload.get("id"):
                    return item
            raise RuntimeError(f"no matching json-rpc response id: {payload.get('id')}")
        _dispatch_message(parsed, self.notification_handler, self.request_handler)
        return parsed

    def _listen_for_notifications(self) -> None:
        request = Request(
            self.url,
            method="GET",
            headers={
                "Accept": "text/event-stream",
                **self.headers,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                if "text/event-stream" not in content_type:
                    return
                _read_sse_notifications(response, self.notification_handler, self.request_handler, self._listener_stop)
        except BaseException as exc:
            if not self._listener_stop.is_set():
                self._listener_error = exc
                if self.failure_handler is not None:
                    self.failure_handler(exc)


def _parse_sse_response(raw: str, expected_id, notification_handler: NotificationHandler | None, request_handler: RequestHandler | None = None) -> dict[str, Any]:
    for event in raw.split("\n\n"):
        data_lines = []
        for line in event.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if not data_lines:
            continue
        data = "\n".join(data_lines)
        if data == "[DONE]":
            continue
        parsed = json.loads(data)
        _dispatch_message(parsed, notification_handler, request_handler)
        if parsed.get("id") == expected_id:
            return parsed
    raise RuntimeError(f"no matching SSE json-rpc response id: {expected_id}")


def _read_sse_notifications(response, notification_handler: NotificationHandler | None, request_handler: RequestHandler | None, stop_event: threading.Event) -> None:
    data_lines = []
    while not stop_event.is_set():
        raw_line = response.readline()
        if raw_line == b"" or raw_line == "":
            return
        line = raw_line.decode("utf-8").strip() if isinstance(raw_line, bytes) else raw_line.strip()
        if not line:
            _dispatch_sse_data(data_lines, notification_handler, request_handler)
            data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    _dispatch_sse_data(data_lines, notification_handler, request_handler)


def _dispatch_sse_data(data_lines: list[str], notification_handler: NotificationHandler | None, request_handler: RequestHandler | None) -> None:
    if not data_lines:
        return
    data = "\n".join(data_lines)
    if data == "[DONE]":
        return
    _dispatch_message(json.loads(data), notification_handler, request_handler)


def _dispatch_message(message: Any, notification_handler: NotificationHandler | None, request_handler: RequestHandler | None) -> None:
    if not isinstance(message, dict):
        return
    if isinstance(message.get("method"), str):
        if "id" in message:
            _dispatch_request(message, request_handler)
            return
        if notification_handler is not None:
            notification_handler(message)


def _dispatch_request(message: dict[str, Any], request_handler: RequestHandler | None) -> dict[str, Any]:
    request_id = message.get("id")
    if request_handler is None:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "request handler not configured"}}
    try:
        return {"jsonrpc": "2.0", "id": request_id, "result": request_handler(message)}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}
