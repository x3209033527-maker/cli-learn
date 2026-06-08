from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class McpServerConfig:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    disabled: bool = False
    auto_restart: bool = False
    auto_restart_delay_seconds: float = 0.0
    headers: dict[str, str] = field(default_factory=dict)
    oauth: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_json(name: str, data: dict[str, Any]) -> "McpServerConfig":
        delay = data.get("autoRestartDelaySeconds", data.get("auto_restart_delay_seconds", 0.0))
        return McpServerConfig(
            name=name,
            command=data.get("command"),
            args=[str(item) for item in data.get("args", [])],
            url=data.get("url"),
            disabled=bool(data.get("disabled", False)),
            auto_restart=bool(data.get("autoRestart", data.get("auto_restart", False))),
            auto_restart_delay_seconds=float(delay or 0.0),
            headers={str(key): str(value) for key, value in (data.get("headers") or {}).items()} if isinstance(data.get("headers"), dict) else {},
            oauth={str(key): str(value) for key, value in (data.get("oauth") or {}).items()} if isinstance(data.get("oauth"), dict) else {},
        )

    @property
    def is_stdio(self) -> bool:
        return bool(self.command)

    @property
    def is_http(self) -> bool:
        return bool(self.url)

    def command_line(self) -> list[str]:
        if not self.command:
            raise ValueError(f"MCP server {self.name} has no command")
        return [self.command, *self.args]

    def http_headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        token = self.oauth.get("accessToken", "") or _env_token(self.oauth.get("tokenEnv", ""))
        if token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {token}"
        return headers


def _env_token(name: str) -> str:
    return os.getenv(name, "") if name else ""


class McpConfigLoader:
    def __init__(self, project_dir: str | Path, user_config: str | Path | None = None):
        self.project_dir = Path(project_dir).resolve()
        self.user_config = Path(user_config) if user_config else Path.home() / ".paicli-py" / "mcp.json"
        self.project_config = self.project_dir / ".paicli-py" / "mcp.json"

    def load(self) -> dict[str, McpServerConfig]:
        configs: dict[str, McpServerConfig] = {}
        for path in [self.user_config, self.project_config]:
            for name, config in self._read_file(path).items():
                configs[name] = config
        return configs

    def _read_file(self, path: Path) -> dict[str, McpServerConfig]:
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        servers = raw.get("mcpServers", {}) if isinstance(raw, dict) else {}
        return {
            name: McpServerConfig.from_json(name, data)
            for name, data in servers.items()
            if isinstance(data, dict)
        }
