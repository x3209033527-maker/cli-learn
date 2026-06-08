from __future__ import annotations

import threading
from dataclasses import dataclass
from dataclasses import field
from typing import Any, Callable, Protocol


class JsonRpcError(RuntimeError):
    pass


NotificationHandler = Callable[[dict[str, Any]], None]
FailureHandler = Callable[[BaseException], None]
RequestHandler = Callable[[dict[str, Any]], Any]


class JsonRpcTransport(Protocol):
    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass
class JsonRpcClient:
    transport: JsonRpcTransport
    next_id: int = 1
    _id_lock: threading.Lock = field(default_factory=threading.Lock)

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._id_lock:
            request_id = self.next_id
            self.next_id += 1
        response = self.transport.request({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        })
        if response.get("id") != request_id:
            raise JsonRpcError(f"mismatched json-rpc id: expected {request_id}, got {response.get('id')}")
        if "error" in response:
            error = response["error"]
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise JsonRpcError(message)
        return response.get("result")
