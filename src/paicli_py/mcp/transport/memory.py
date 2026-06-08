from __future__ import annotations

from typing import Any, Callable

from ..jsonrpc import FailureHandler, NotificationHandler, RequestHandler


class InMemoryTransport:
    def __init__(self):
        self.handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self.requests: list[dict[str, Any]] = []
        self.notification_handler: NotificationHandler | None = None
        self.failure_handler: FailureHandler | None = None
        self.request_handler: RequestHandler | None = None

    def on(self, method: str, handler: Callable[[dict[str, Any]], Any]) -> "InMemoryTransport":
        self.handlers[method] = handler
        return self

    def set_notification_handler(self, handler: NotificationHandler | None) -> None:
        self.notification_handler = handler

    def set_failure_handler(self, handler: FailureHandler | None) -> None:
        self.failure_handler = handler

    def set_request_handler(self, handler: RequestHandler | None) -> None:
        self.request_handler = handler

    def emit_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self.notification_handler is None:
            return
        self.notification_handler({
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        })

    def emit_failure(self, exc: BaseException) -> None:
        if self.failure_handler is not None:
            self.failure_handler(exc)

    def emit_request(self, method: str, params: dict[str, Any] | None = None, request_id: Any = 1) -> dict[str, Any]:
        if self.request_handler is None:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "request handler not configured"}}
        try:
            return {"jsonrpc": "2.0", "id": request_id, "result": self.request_handler({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            })}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        method = payload["method"]
        request_id = payload["id"]
        if method not in self.handlers:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        try:
            result = self.handlers[method](payload.get("params") or {})
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}
