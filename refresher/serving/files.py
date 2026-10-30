"""GET: what the workspace holds (submissions, the library, uploads and originals) and the static files."""
import logging
from http import HTTPStatus

from refresher import library
from refresher.serving.routes import CSP, FILES, SUBMISSION, UPLOAD

logger = logging.getLogger("refresher.app")


class Files:
    """Submissions and the library, saved originals and uploads, then every file in FILES."""

    def _get_files(self, path: str, args: dict):
        if path == "/api/submissions":
            return self._json(HTTPStatus.OK, self.workspace.list_jobs())
        if path == "/api/library":
            return self._json(HTTPStatus.OK, library.catalog(self.vault_path, self.workspace.list_jobs()))
        if path == "/api/library/source":
            result = library.source(self.vault_path, args.get("id", ""))
            return self._json(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                              result or {"error": "This source is no longer available. Return to your Library."})
        if path == "/api/library/original":
            try:
                content = library.original(self.vault_path, args.get("id", ""), args.get("kind", ""))
            except (OSError, ValueError):
                return self._json(HTTPStatus.CONFLICT, {"error": "The original could not be verified."})
            if content is not None:
                data, suffix = content
                return self._send(HTTPStatus.OK, data, "application/octet-stream",
                                  headers=[("Content-Disposition", f'attachment; filename="saved-original{suffix}"')])
            return self._json(HTTPStatus.NOT_FOUND, {"error": "Original not found."})
        if match := SUBMISSION.match(path):
            result = self.workspace.detail(match[1])
            return self._json(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                              result or {"error": "Submission not found."})
        if (match := UPLOAD.match(path)) and (upload := self.workspace.vault / "uploads" / match[1]).is_file():
            return self._send(HTTPStatus.OK, upload.read_bytes(), "application/octet-stream",
                              headers=[("Content-Disposition", 'attachment; filename="original-upload"')])
        if path in FILES:
            file, kind = FILES[path]
            csp = "atlas" if path.startswith(("/atlas/", "/review/")) else "topic" if path in ("/topic/", "/brief/") else "page"
            return self._send(HTTPStatus.OK, file.read_bytes(), kind, CSP[csp])
