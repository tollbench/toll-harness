from __future__ import annotations

import http.client
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


def _public_addresses(url: str) -> tuple[str, int, list[str]]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public HTTP and HTTPS URLs are supported")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = []
    for result in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(result[4][0])
        if not address.is_global:
            raise ValueError("Private, loopback, and link-local addresses are blocked")
        if str(address) not in addresses:
            addresses.append(str(address))
    if not addresses:
        raise ValueError("The public hostname did not resolve")
    return parsed.hostname, port, addresses


def _validate_public_url(url: str) -> None:
    _public_addresses(url)


def _pinned_connection(kind: Any, addresses: list[str], port: int) -> Any:
    """Connect only to the validated addresses; retain the hostname for TLS/Host."""
    def factory(host: str, **kwargs: Any) -> Any:
        connection = kind(host, **kwargs)
        def connect(address: Any, timeout: Any, source_address: Any = None) -> Any:
            last_error = None
            for ip in addresses:
                try:
                    return socket.create_connection((ip, port), timeout, source_address)
                except OSError as exc:
                    last_error = exc
            raise last_error or OSError("No public address available")
        connection._create_connection = connect
        return connection
    return factory


class PublicHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, addresses: list[str], port: int):
        super().__init__()
        self.connection = _pinned_connection(http.client.HTTPConnection, addresses, port)

    def http_open(self, request: Any) -> Any:
        return self.do_open(self.connection, request)


class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, addresses: list[str], port: int):
        super().__init__()
        self.connection = _pinned_connection(http.client.HTTPSConnection, addresses, port)

    def https_open(self, request: Any) -> Any:
        return self.do_open(self.connection, request, context=self._context)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise ValueError("Redirects are disabled; fetch the destination URL explicitly")


class BasicWebProvider(WebProvider):
    """Small local fetch provider. Search requires an operator-supplied implementation."""

    def __init__(self, *, timeout_seconds: float = 15, max_bytes: int = 1_000_000):
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes

    def fetch(self, url: str) -> dict[str, Any]:
        _host, port, addresses = _public_addresses(url)
        request = urllib.request.Request(url, headers={"User-Agent": "TollHarness/0.1"})
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirectHandler(),
            PublicHTTPHandler(addresses, port), PublicHTTPSHandler(addresses, port),
        )
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
