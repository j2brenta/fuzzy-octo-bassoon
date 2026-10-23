"""Bounded public HTML downloads; every redirect is validated before connecting."""

from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPSConnection, HTTPException
import ipaddress
import socket
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_HTML_BYTES = 2_000_000
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 15


class WebError(ValueError):
    """Safe fetch/parse failure; originals remain available for retry."""


@dataclass(frozen=True)
class HtmlPage:
    requested_url: str
    final_url: str
    content: bytes
    fetched_at: str


def normalize_url(url: str) -> str:
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise WebError("Article URL contains invalid characters.")
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError
        host = parts.hostname.encode("idna").decode("ascii").lower()
        port = parts.port
        if port is not None and port not in {80, 443}:
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        default = 443 if parts.scheme == "https" else 80
        if port is not None and port != default:
            authority += f":{port}"
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
        return urlunsplit((parts.scheme, authority, path, query, ""))
    except (ValueError, UnicodeError):
        raise WebError("Use a public HTTP(S) article URL without credentials, on port 80 or 443.") from None


def _public_address(host: str, port: int) -> str:
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(row[4][0]) for row in addresses]
    except (OSError, ValueError):
        raise WebError("Could not resolve the article host.") from None
    if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
        raise WebError("Article links must resolve only to public internet addresses.")
    return str(ips[0])


class _PublicHTTPConnection(HTTPConnection):
    def connect(self):
        self.sock = socket.create_connection((self.public_address, self.port), self.timeout)


class _PublicHTTPSConnection(HTTPSConnection):
    def connect(self):
        sock = socket.create_connection((self.public_address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def _connection(url: str):
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    address = _public_address(parts.hostname, port)
    cls = _PublicHTTPSConnection if parts.scheme == "https" else _PublicHTTPConnection
    connection = cls(parts.hostname, port, timeout=TIMEOUT_SECONDS)
    # Pin the validated address; TLS still verifies the original hostname.
    connection.public_address = address
    return connection


def fetch_html(url: str) -> HtmlPage:
    requested = current = normalize_url(url)
    try:
        for hop in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            connection = _connection(current)
            try:
                target = urlunsplit(("", "", parts.path, parts.query, ""))
                connection.request("GET", target, headers={
                    "User-Agent": "Refresher/0.1 (public article reader)",
                    "Accept": "text/html, application/xhtml+xml",
                    "Accept-Encoding": "identity",
                })
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location or hop == MAX_REDIRECTS:
                        raise WebError("Article redirect was missing or exceeded the redirect limit.")
                    current = normalize_url(urljoin(current, location))
                    continue
                if response.status == 403:
                    raise WebError("This site blocked the automated download (HTTP 403). Open the link in your browser and paste the article text into Telegram.")
                if response.status != 200:
                    raise WebError(f"Article download returned HTTP {response.status}; paste its text if access is restricted.")
                content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    raise WebError("This link is not an HTML article; PDF and media parsing come later.")
                if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise WebError("Article server returned unsupported compressed content.")
                body = response.read(MAX_HTML_BYTES + 1)
                if len(body) > MAX_HTML_BYTES:
                    raise WebError("Article HTML exceeds the 2 MB download limit.")
                if not body.strip():
                    raise WebError("Article server returned an empty page.")
                return HtmlPage(requested, current, body, datetime.now(timezone.utc).isoformat())
            finally:
                connection.close()
    except WebError:
        raise
    except (OSError, HTTPException, UnicodeError, ValueError):
        raise WebError("Could not download the article; check the link or paste its text.") from None
    raise WebError("Article download did not complete.")
