"""The request handler every route shares: who may ask, and how an answer is sent.

REFRESHER_LOGIN=user:password puts every page and API behind HTTP Basic auth (only /healthz stays open); serve it
over HTTPS only. With a login set, the address in REFRESHER_APP_URL is answered too, as a Host and as an Origin:
a public address is answered only behind a login. Its host may start with "*." for any one name under a domain, as
a Nebius endpoint's URL is known only once it runs: https://*.tunnel.applications.eu-west1.nebius.cloud"""
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

from refresher.serving.routes import CSP, HTML

logger = logging.getLogger("refresher.app")


def login() -> str:
    return os.environ.get("REFRESHER_LOGIN", "").strip()


def public() -> tuple[str, str] | None:
    """(host, scheme) of REFRESHER_APP_URL when a login guards it; None otherwise."""
    url = urlsplit(os.environ.get("REFRESHER_APP_URL", "").strip())
    if not login() or url.scheme not in ("http", "https") or not url.netloc:
        return None
    return url.netloc.lower(), url.scheme


def matches(host: str, pattern: str) -> bool:
    host = host.lower()
    if pattern.startswith("*."):
        name, _, domain = host.partition(".")
        return bool(name) and domain == pattern[2:]
    return host == pattern


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

    def _public(self) -> str | None:
        """The request's origin when its Host is the public address, else None."""
        address, host = public(), self.headers.get("Host", "")
        return f"{address[1]}://{host.lower()}" if address and matches(host, address[0]) else None

    def _origins(self) -> set[str]:
        return {f"http://{h}" for h in self._hosts()} | ({self._public()} - {None})

    def _local(self) -> bool:
        return self.headers.get("Host", "") in self._hosts() or self._public() is not None

    def _from_app(self) -> bool:
        """A change from the app's own page: a known Host, the X-Refresher header and, when the browser sends one,
        a same-origin Origin."""
        origin = self.headers.get("Origin")
        return self._local() and self.headers.get("X-Refresher") == "1" and (origin is None or origin in self._origins())

    def _authorized(self) -> bool:
        expected = login()
        if not expected:
            return True
        scheme, _, value = self.headers.get("Authorization", "").partition(" ")
        try:
            given = base64.b64decode(value.strip(), validate=True)
        except (binascii.Error, ValueError):
            return False
        return scheme.lower() == "basic" and hmac.compare_digest(given, expected.encode())

    def _ask_login(self):
        self.log("serve: login refused")
        return self._send(HTTPStatus.UNAUTHORIZED, json.dumps({"error": "Log in to open Refresher."}).encode(),
                          "application/json; charset=utf-8",
                          headers=[("WWW-Authenticate", 'Basic realm="Refresher", charset="UTF-8"')])

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
