"""refresher serve: the map on http://127.0.0.1:8765 with buttons to decide the review's proposals.

Local only: it binds to 127.0.0.1, answers only to a localhost Host header, and takes a decision only with the
X-Refresher header and a same-origin Origin, so another site open in the browser cannot post one. Every page load
replays the vault, so what you see is what a rebuild would write. A decision goes through vault.decide(): the token the
page was given must still be current, or the answer is 409 and the page shows the current version.
"""
import json
import re
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from refresher import graphview, vault

PORT = 8765
MAX_BODY = 16 * 1024
STATIC = {"app.js": ("map/app.js", "text/javascript"), "style.css": ("map/style.css", "text/css"),
          "cytoscape.min.js": ("cytoscape.min.js", "text/javascript")}
DECIDE = re.compile(r"^/api/proposals/(p-[0-9a-f]{16})$")


def snapshot(vault_path: Path, include_unreviewed: bool) -> dict:
    data = graphview.map_data(vault.replay(vault_path, include_unreviewed=include_unreviewed),
                              datetime.now(timezone.utc))
    data["served"] = True
    return data


def rebuild(vault_path: Path, include_unreviewed: bool) -> None:
    """Refresh wiki/ after a decision, as `refresher rebuild` would."""
    at = datetime.now(timezone.utc)
    with vault.lock(vault_path):
        store = vault.replay(vault_path, include_unreviewed=include_unreviewed)
        vault.export(store, vault_path, at)
        graphview.write(store, vault_path, at)


def handler(vault_path: Path, include_unreviewed: bool = True, log=print):
    class Handler(BaseHTTPRequestHandler):
        server_version = "refresher"

        def log_message(self, fmt, *args):  # one line per request, no headers or bodies
            log(f"serve: {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}".rstrip())

        def _local(self) -> bool:
            port = self.server.server_address[1]
            return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _send(self, status: int, body: bytes, kind: str):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value):
            self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            if not self._local():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Open the map through 127.0.0.1 or localhost."})
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                html = graphview.page(snapshot(vault_path, include_unreviewed))
                html = html.replace("connect-src 'none'", "connect-src 'self'")  # the page may post decisions back
                return self._send(HTTPStatus.OK, html.encode(), "text/html; charset=utf-8")
            if path == "/api/data":
                return self._json(HTTPStatus.OK, snapshot(vault_path, include_unreviewed))
            if path == "/api/proposals":
                return self._json(HTTPStatus.OK, snapshot(vault_path, include_unreviewed)["proposals"])
            if path.lstrip("/") in STATIC:
                name, kind = STATIC[path.lstrip("/")]
                return self._send(HTTPStatus.OK, (graphview.ASSETS / name).read_bytes(), kind + "; charset=utf-8")
            return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

        def do_POST(self):
            port = self.server.server_address[1]
            origin = self.headers.get("Origin")
            if not self._local() or self.headers.get("X-Refresher") != "1" or \
                    (origin is not None and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Decisions are taken only from the map itself."})
            match = DECIDE.match(self.path)
            if match is None:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            length = int(self.headers.get("Content-Length") or 0)
            if not self.headers.get("Content-Type", "").startswith("application/json") or not 0 < length <= MAX_BODY:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "Send a small JSON body."})
            try:
                body = json.loads(self.rfile.read(length))
                action, token = body["action"], body["token"]
                rationale = str(body.get("rationale") or "")[:2000]
                if not isinstance(action, str) or not isinstance(token, str):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "The body needs action and token."})
            try:
                decision = vault.decide(vault_path, match.group(1), action, token, rationale)
            except KeyError:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "No such proposal; the review may have replaced it."})
            except vault.StaleToken as error:
                return self._json(HTTPStatus.CONFLICT, {"error": str(error)})
            except ValueError as error:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            rebuild(vault_path, include_unreviewed)
            vault.log(vault_path, decision.at, f"decide: {decision.action} {decision.proposal_id}")
            return self._json(HTTPStatus.OK, {"ok": True, "action": decision.action})

    return Handler


def serve(vault_path: Path, port: int = PORT, include_unreviewed: bool = True) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(vault_path, include_unreviewed))
    print(f"serve: the map of {vault_path} is on http://127.0.0.1:{server.server_address[1]}/ (Ctrl+C stops it)",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("serve: stopped")
    finally:
        server.server_close()
