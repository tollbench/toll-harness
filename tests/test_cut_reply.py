"""A reply cut off partway is a dropped line, never the end of the watch (1.0.2).

What forced it: 23 November 2026, an agent behind a relay network had its 55-second calls to
the bench cut mid-reply. http.client.IncompleteRead is not an OSError, so Bench._call let it
through and the watch died; the agent stayed off the bench until its owner restarted it.
"""

from __future__ import annotations

import socket
import threading

import pytest

from toll_harness.bench import Bench, BenchError


def _server(reply: bytes):
    """A one-shot server: reads a request, sends `reply`, closes."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(4)

    def serve():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            with conn:
                conn.recv(65536)
                conn.sendall(reply)

    threading.Thread(target=serve, daemon=True).start()
    return sock, f"http://127.0.0.1:{sock.getsockname()[1]}"


CUT = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
       b"Content-Length: 500\r\nConnection: close\r\n\r\n" + b'{"items": [')
NOT_HTTP = b"this is not http\r\n\r\n"


@pytest.mark.parametrize("reply", [CUT, NOT_HTTP])
def test_a_broken_reply_is_a_connection_error(reply):
    sock, url = _server(reply)
    try:
        bench = Bench(url, "tok", timeout=5)
        with pytest.raises(BenchError) as caught:
            bench.items(None, 0)
        assert caught.value.status == 0
        assert caught.value.code == "connection_error"
    finally:
        sock.close()


def test_a_cut_file_is_a_connection_error():
    sock, url = _server(CUT)
    try:
        bench = Bench(url, "tok", timeout=5)
        with pytest.raises(BenchError) as caught:
            bench.download("/api/bench/files/cut.bin", lambda block: None, most=10_000, seconds=5)
        assert caught.value.status == 0
        assert caught.value.code == "connection_error"
    finally:
        sock.close()
