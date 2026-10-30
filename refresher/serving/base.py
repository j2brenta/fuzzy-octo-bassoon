"""The request handler every route shares: who may ask, and how an answer is sent."""
import hashlib
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from refresher.serving.routes import CSP, HTML

logger = logging.getLogger("refresher.app")


class Base(BaseHTTPRequestHandler):
    """One request. handler() in refresher/app.py makes the class with the vault, workspace, state, review, the hosts
    it answers besides localhost, and log; the routes are the mixins beside this module."""

    server_version = "refresher"
    vault_path: Path
    allow_hosts: tuple = ()

    def log_message(self, fmt, *args):  # one line per request, no headers or bodies
        self.log(f"serve: {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}".rstrip())

    def _hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"{h}:{port}" for h in ("127.0.0.1", "localhost")} | \
            {h if ":" in h else f"{h}:{port}" for h in self.allow_hosts}

    def _local(self) -> bool:
        return self.headers.get("Host", "") in self._hosts()

    def _send(self, status: int, body: bytes, kind: str, csp: str = CSP["page"], headers=()):
        if kind == HTML:
            workspace_id = hashlib.sha256(str(self.vault_path.resolve()).encode()).hexdigest()[:16]
            body = body.replace(b"<body", f'<body data-workspace="{workspace_id}"'.encode(), 1)
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", csp)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
        return True  # every route answers through here: a route that answered returns True

    def _json(self, status: int, value):
        return self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _redirect(self, to: str):
        return self._send(HTTPStatus.FOUND, b"", "text/plain", headers=[("Location", to)])
