"""Receive the CLI's OpenTelemetry log events on a private, per-run localhost endpoint."""

import re
import secrets
import struct
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer

MAX_BODY_BYTES = 4_000_000
Attributes = dict[str, object]


def varint(data: bytes, index: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if index >= len(data) or shift > 63:
            raise ValueError("Truncated varint")
        byte = data[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, index
        shift += 7


def fields(data: bytes) -> Iterator[tuple[int, int, object]]:
    index = 0
    while index < len(data):
        key, index = varint(data, index)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, index = varint(data, index)
        elif wire == 1:
            value, index = data[index : index + 8], index + 8
        elif wire == 2:
            length, index = varint(data, index)
            value, index = data[index : index + length], index + length
        elif wire == 5:
            value, index = data[index : index + 4], index + 4
        else:
            raise ValueError("Unsupported protobuf wire type")
        if index > len(data):
            raise ValueError("Truncated protobuf field")
        yield number, wire, value


def any_value(data: bytes) -> object:
    result: object = None
    for number, wire, value in fields(data):
        if number == 1 and wire == 2:
            result = value.decode("utf-8", "replace")
        elif number == 2 and wire == 0:
            result = bool(value)
        elif number == 3 and wire == 0:
            result = value - (1 << 64) if value >= 1 << 63 else value
        elif number == 4 and wire == 1:
            result = struct.unpack("<d", value)[0]
    return result


def key_value(data: bytes) -> tuple[str, object]:
    key, result = "", None
    for number, wire, value in fields(data):
        if number == 1 and wire == 2:
            key = value.decode("utf-8", "replace")
        elif number == 2 and wire == 2:
            result = any_value(value)
    return key, result


def log_record(data: bytes, resource: Attributes) -> Attributes:
    record = dict(resource)
    for number, wire, value in fields(data):
        if number == 6 and wire == 2:
            key, item = key_value(value)
            record[key] = item
        elif number == 5 and wire == 2 and "event.name" not in record:
            body = any_value(value)
            if isinstance(body, str):
                record["event.name"] = body
        elif number == 12 and wire == 2 and value:
            record["event.name"] = value.decode("utf-8", "replace")
    return record


def log_records(body: bytes) -> list[Attributes]:
    """Decode an OTLP/HTTP protobuf ExportLogsServiceRequest into flat attribute maps."""
    records = []
    for number, wire, resource_logs in fields(body):
        if number != 1 or wire != 2:
            continue
        resource: Attributes = {}
        scopes = []
        for field, kind, value in fields(resource_logs):
            if field == 1 and kind == 2:
                for item, item_kind, entry in fields(value):
                    if item == 1 and item_kind == 2:
                        key, attribute = key_value(entry)
                        resource[key] = attribute
            elif field == 2 and kind == 2:
                scopes.append(value)
        for scope in scopes:
            for field, kind, value in fields(scope):
                if field == 2 and kind == 2:
                    records.append(log_record(value, resource))
    return records


def quota_exhausted(record: Attributes) -> bool:
    """A typed quota failure; rate limits carry a retry delay and are not quota exhaustion."""
    kind = re.sub(r"[^a-z]", "", str(record.get("error_kind", "")).lower())
    return (
        record.get("event.name") == "api_error"
        and kind == "resourceexhausted"
        and record.get("retry_after_ms") is None
    )


class LoopbackServer(ThreadingHTTPServer):
    """An HTTP server on 127.0.0.1 that skips HTTPServer's reverse DNS lookup.

    `HTTPServer.server_bind` calls `socket.getfqdn`, which can stall for seconds where DNS is
    slow (CI runners), delaying every terminal launch before the CLI starts.
    """

    daemon_threads = True

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class Receiver:
    """Accept log exports only at a random path, so other local processes cannot inject events."""

    def __init__(self, handle: Callable[[Attributes], None]) -> None:
        token = secrets.token_hex(16)
        path = f"/{token}/v1/logs"

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or -1)
                if (
                    self.path != path
                    or self.headers.get("Content-Encoding", "identity") != "identity"
                    or not 0 <= length <= MAX_BODY_BYTES
                ):
                    self.send_response(404 if self.path != path else 400)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", "0")
                self.end_headers()
                try:
                    records = log_records(body)
                except ValueError:
                    return
                for record in records:
                    handle(record)

            def log_message(self, *_args: object) -> None:
                pass

        self.server = LoopbackServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}{path}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "Receiver":
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
