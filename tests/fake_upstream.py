"""An offline stand-in for the Devin API server, keyed by each fake login's API key."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INFERENCE = "/exa.api_server_pb.ApiServerService/GetChatMessage"
QUOTA = {
    "error": {
        "code": "failed_precondition",
        "message": "Your daily usage quota has been exhausted. Visit "
        "https://app.devin.ai/settings/usage to purchase on-demand usage. (trace ID: 1)",
    }
}


def frame(flags: int, payload: bytes) -> bytes:
    return bytes([flags]) + len(payload).to_bytes(4, "big") + payload


def reply(account: str) -> bytes:
    return frame(0, f"reply from {account}".encode()) + frame(2, b"{}")


def api_key(name: str) -> str:
    # Every saved key has the same length, like the real 189-character keys.
    return f"key-{name}".ljust(48, "x")


class Upstream:
    def __init__(self, accounts):
        self.keys = {api_key(name): name for name in accounts}
        self.exhausted = set()
        self.first_frame = None
        # Empty protobuf bodies decode as default messages in the installed CLI.
        self.empty_status = False
        self.requests = []
        self.lock = threading.Lock()
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                header = upstream.owner(self.headers.get("Authorization", ""))
                with upstream.lock:
                    upstream.requests.append(
                        {
                            "path": self.path,
                            "header": header,
                            "body": upstream.owner(body.decode(errors="replace")),
                            "raw": body,
                        }
                    )
                if self.path != INFERENCE:
                    status = b"" if upstream.empty_status else f"status {header}".encode()
                    return self.send(200, "application/proto", status)
                if header in upstream.exhausted:
                    payload = frame(2, json.dumps(QUOTA).encode())
                else:
                    payload = upstream.first_frame or reply(header)
                self.send(200, "application/connect+proto", payload, chunked=True)

            def send(self, status, content_type, payload, *, chunked=False):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                if chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(payload), payload))
                else:
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def owner(self, text: str) -> str | None:
        return next((name for key, name in self.keys.items() if key in text), None)

    def inference(self):
        with self.lock:
            return [(r["header"], r["body"]) for r in self.requests if r["path"] == INFERENCE]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def credentials(self, name: str) -> str:
        return f'windsurf_api_key = "{api_key(name)}"\napi_server_url = "{self.url}"\n'
