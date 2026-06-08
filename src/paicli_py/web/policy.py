from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urlparse


class NetworkPolicyError(RuntimeError):
    pass


class NetworkPolicy:
    BLOCKED_SCHEMES = {"file", "ftp"}
    BLOCKED_HOSTS = {"localhost"}

    def __init__(self, window_seconds: int = 60, max_requests: int = 30, clock=None):
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self.clock = clock or time.monotonic
        self._window_start = 0.0
        self._request_count = 0

    def validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise NetworkPolicyError(f"unsupported URL scheme: {parsed.scheme}")
        host = parsed.hostname
        if not host:
            raise NetworkPolicyError("URL host is required")
        if host.lower() in self.BLOCKED_HOSTS:
            raise NetworkPolicyError(f"blocked host: {host}")
        for address in self._resolve(host):
            if address.is_loopback or address.is_private or address.is_link_local or address.is_multicast:
                raise NetworkPolicyError(f"blocked private address: {address}")

    def acquire(self) -> None:
        now = self.clock()
        if self._window_start == 0.0 or now - self._window_start >= self.window_seconds:
            self._window_start = now
            self._request_count = 1
            return
        self._request_count += 1
        if self._request_count > self.max_requests:
            reset_in = max(1, int(self.window_seconds - (now - self._window_start)))
            raise NetworkPolicyError(
                f"request rate limit exceeded: {self.max_requests} requests per "
                f"{self.window_seconds}s, retry in about {reset_in}s"
            )

    def _resolve(self, host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            pass
        addresses = []
        try:
            for family, _, _, _, sockaddr in socket.getaddrinfo(host, None):
                raw = sockaddr[0]
                addresses.append(ipaddress.ip_address(raw))
        except socket.gaierror:
            return []
        return addresses
