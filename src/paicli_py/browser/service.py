from __future__ import annotations

import fnmatch
import json
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class BrowserMode(str, Enum):
    ISOLATED = "isolated"
    SHARED = "shared"


@dataclass
class BrowserSession:
    mode: BrowserMode = BrowserMode.ISOLATED
    browser_url: str = ""
    last_navigated_url: str = ""
    agent_opened_tabs: set[str] = field(default_factory=set)

    def switch_to_isolated(self) -> None:
        self.mode = BrowserMode.ISOLATED
        self.browser_url = ""
        self.last_navigated_url = ""
        self.agent_opened_tabs.clear()

    def switch_to_shared(self, browser_url: str) -> None:
        self.mode = BrowserMode.SHARED
        self.browser_url = browser_url.rstrip("/")
        self.last_navigated_url = ""
        self.agent_opened_tabs.clear()

    def remember_navigation(self, url: str) -> None:
        if url and url.strip():
            self.last_navigated_url = url.strip()

    def record_opened_tab(self, page_id: str) -> None:
        if page_id and page_id.strip():
            self.agent_opened_tabs.add(page_id.strip())


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    browser_url: str = ""
    message: str = "ok"


class BrowserConnectivityCheck:
    def __init__(self, timeout: int = 2):
        self.timeout = timeout

    def probe(self, port: int = 9222) -> ProbeResult:
        if port < 1024 or port > 65535:
            return ProbeResult(False, message="port must be between 1024 and 65535")
        browser_url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{browser_url}/json/version", timeout=self.timeout) as response:
                if response.status >= 400:
                    return ProbeResult(False, message=f"HTTP {response.status}")
                return ProbeResult(True, browser_url)
        except Exception as exc:
            return ProbeResult(False, message=str(exc) or "connection failed")

    def list_tabs(self, browser_url: str) -> list[dict[str, Any]]:
        if not browser_url:
            return []
        with urllib.request.urlopen(browser_url.rstrip("/") + "/json", timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "[]")
        return payload if isinstance(payload, list) else []


@dataclass(frozen=True)
class SensitiveMatch:
    matched: bool
    pattern: str = ""


class SensitivePagePolicy:
    DEFAULT_PATTERNS = [
        "*://*.bank.*/*",
        "*://*.alipay.com/*",
        "*://*.paypal.com/*",
        "*://*.stripe.com/*",
        "*://github.com/settings/*",
        "*://*.github.com/settings/*",
        "*://github.com/*/settings/*",
        "*://*.github.com/*/settings/*",
        "*://paypal.com/*",
        "*://*.feishu.cn/admin/*",
        "*://*.larksuite.com/admin/*",
        "*://*.console.cloud.google.com/*",
        "*://*.console.aws.amazon.com/*",
        "*://*.portal.azure.com/*",
    ]

    def __init__(self, user_rules_file: str | Path | None = None, patterns: list[str] | None = None):
        self.patterns = list(patterns or self.DEFAULT_PATTERNS)
        if user_rules_file:
            self.patterns.extend(_load_user_patterns(Path(user_rules_file)))

    def match(self, url: str | None) -> SensitiveMatch:
        if not url:
            return SensitiveMatch(False)
        normalized = url.lower()
        for pattern in self.patterns:
            if fnmatch.fnmatchcase(normalized, pattern.lower()):
                return SensitiveMatch(True, pattern)
        return SensitiveMatch(False)

    def is_sensitive(self, url: str | None) -> bool:
        return self.match(url).matched


@dataclass(frozen=True)
class BrowserCheckResult:
    blocked: bool = False
    reason: str = ""
    requires_approval: bool = False
    notice: str = ""
    sensitive: bool = False
    target_url: str = ""


class BrowserService:
    WRITE_TOOLS = {
        "click",
        "drag",
        "fill",
        "fill_form",
        "handle_dialog",
        "hover",
        "press_key",
        "resize_page",
        "upload_file",
        "evaluate_script",
    }

    def __init__(
        self,
        session: BrowserSession | None = None,
        connectivity: BrowserConnectivityCheck | None = None,
        policy: SensitivePagePolicy | None = None,
    ):
        self.session = session or BrowserSession()
        self.connectivity = connectivity or BrowserConnectivityCheck()
        self.policy = policy or SensitivePagePolicy()

    def status(self) -> str:
        probe = self.connectivity.probe(9222)
        lines = [
            f"Browser mode: {self.session.mode.value}" + (f" ({self.session.browser_url})" if self.session.browser_url else ""),
            "Remote debugging probe: " + (probe.browser_url if probe.ok else probe.message),
        ]
        if self.session.last_navigated_url:
            lines.append(f"Last navigated URL: {self.session.last_navigated_url}")
        return "\n".join(lines)

    def connect(self, port: int = 9222) -> str:
        probe = self.connectivity.probe(port)
        if not probe.ok:
            return f"browser connect failed: {probe.message}"
        self.session.switch_to_shared(probe.browser_url)
        return f"browser connected: {probe.browser_url}"

    def disconnect(self) -> str:
        self.session.switch_to_isolated()
        return "browser disconnected: isolated mode"

    def tabs(self) -> str:
        if self.session.mode != BrowserMode.SHARED or not self.session.browser_url:
            return "browser is isolated; use /browser connect [port] first"
        try:
            tabs = self.connectivity.list_tabs(self.session.browser_url)
        except Exception as exc:
            return f"browser tabs failed: {exc}"
        if not tabs:
            return "No browser tabs."
        lines = [f"Browser tabs: {len(tabs)}"]
        for tab in tabs:
            title = str(tab.get("title", "")).strip()
            url = str(tab.get("url", "")).strip()
            tab_id = str(tab.get("id", "")).strip()
            lines.append(f"- {tab_id or '-'}  {title or '-'}  {url or '-'}")
        return "\n".join(lines)

    def check_tool(self, tool_name: str, arguments: dict[str, Any], mutate_session: bool = True) -> BrowserCheckResult:
        local = _local_chrome_tool_name(tool_name)
        if local is None:
            return BrowserCheckResult()
        target_url = _target_url(local, arguments) or self.session.last_navigated_url
        match = self.policy.match(target_url)
        if local == "close_page" and self.session.mode == BrowserMode.SHARED:
            page_id = _page_id(arguments)
            if page_id not in self.session.agent_opened_tabs:
                return BrowserCheckResult(True, "refusing to close a shared browser tab not opened by PaiCLI", sensitive=match.matched, target_url=target_url or "")
        if match.matched and local in self.WRITE_TOOLS:
            return BrowserCheckResult(False, requires_approval=True, notice=f"sensitive page matched {match.pattern}", sensitive=True, target_url=target_url or "")
        if mutate_session:
            self.apply_tool_result(tool_name, arguments, "")
        return BrowserCheckResult(False, sensitive=match.matched, target_url=target_url or "")

    def apply_tool_result(self, tool_name: str, arguments: dict[str, Any], result: str = "") -> None:
        local = _local_chrome_tool_name(tool_name)
        if local is None:
            return
        target = _target_url(local, arguments)
        if target:
            self.session.remember_navigation(target)
        if local == "new_page":
            page_id = _page_id(arguments) or _extract_page_id(result)
            self.session.record_opened_tab(page_id)


def handle_browser_command(service: BrowserService, payload: str) -> str:
    normalized = (payload or "status").strip()
    if not normalized or normalized == "status":
        return service.status()
    if normalized == "disconnect":
        return service.disconnect()
    if normalized == "tabs":
        return service.tabs()
    if normalized == "connect":
        return service.connect(9222)
    if normalized.startswith("connect "):
        try:
            return service.connect(int(normalized.split(maxsplit=1)[1]))
        except ValueError:
            return "Usage: /browser connect [port]"
    return "Usage: /browser [status | connect [port] | disconnect | tabs]"


def _load_user_patterns(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    except OSError:
        return []


def _local_chrome_tool_name(tool_name: str) -> str | None:
    prefix = "mcp__chrome-devtools__"
    return tool_name[len(prefix):] if tool_name and tool_name.startswith(prefix) else None


def _target_url(local_tool: str, arguments: dict[str, Any]) -> str:
    if local_tool not in {"navigate_page", "new_page"}:
        return ""
    return str(arguments.get("url", "") or "").strip()


def _page_id(arguments: dict[str, Any]) -> str:
    for key in ("pageIdx", "pageId", "uid"):
        value = str(arguments.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _extract_page_id(result: str) -> str:
    for token in str(result or "").replace(",", " ").split():
        if token.startswith("page_") or token.startswith("page-"):
            return token
    return ""
