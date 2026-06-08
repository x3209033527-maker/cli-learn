from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


NotificationHandler = Callable[[str, dict[str, Any]], None]


@dataclass
class NotificationRouter:
    handler: NotificationHandler

    def route(self, server_name: str, message: dict[str, Any]) -> bool:
        if "id" in message:
            return False
        method = message.get("method")
        if not isinstance(method, str):
            return False
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        self.handler(server_name, {"method": method, "params": params})
        return True

