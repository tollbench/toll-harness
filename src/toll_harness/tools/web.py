from __future__ import annotations

import ipaddress
import socket
import urllib.request
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse


class WebProvider(ABC):
    @abstractmethod
    def fetch(self, url: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        raise NotImplementedError


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public HTTP and HTTPS URLs are supported")
    for result in socket.getaddrinfo(parsed.hostname, parsed.port or 443):
        address = ipaddress.ip_address(result[4][0])
        if not address.is_global:
            raise ValueError("Private, loopback, and link-local addresses are blocked")


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise ValueError("Redirects are disabled; fetch the destination URL explicitly")


class BasicWebProvider(WebProvider):
    """Small local fetch provider. Search requires an operator-supplied implementation."""

    def __init__(self, *, timeout_seconds: float = 15, max_bytes: int = 1_000_000):
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes

    def fetch(self, url: str) -> dict[str, Any]:
        _validate_public_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": "TollHarness/0.1"})
        opener = urllib.request.build_opener(NoRedirectHandler())
        with opener.open(request, timeout=self.timeout_seconds) as response:
            content = response.read(self.max_bytes + 1)
            if len(content) > self.max_bytes:
                raise ValueError("Response exceeds configured byte limit")
            content_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"
        return {
            "url": url,
            "status": response.status,
            "content_type": content_type,
            "content": content.decode(charset, errors="replace"),
        }

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        raise RuntimeError("No search backend is configured")
