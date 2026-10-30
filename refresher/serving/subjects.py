"""GET: a subject read as a brief, a thread, a dossier or a scored subject, and one source or entity within it."""
import logging
from datetime import datetime, timezone
from http import HTTPStatus

from refresher import brief, subject, threads
from refresher.serving.routes import BRIEF_SOURCE, SOURCE, THREAD

logger = logging.getLogger("refresher.app")


class Subjects:
    """The brief and its threads, the dossier, the subject with its plan, results and path, and a source context."""

    def _get_subjects(self, path: str, args: dict):
        if path in ("/api/brief", "/api/brief.md"):
            try:
                at = datetime.fromisoformat(args["at"]) if args.get("at") else None
            except ValueError:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "at must be an ISO date."})
            at = at.replace(tzinfo=at.tzinfo or timezone.utc) if at else None
            c = self.state.brief(args.get("id") or None, args.get("q") or None, at)
            if c is None:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Nothing in your knowledge matches that yet.",
                                                         "named": self.state.named(args.get("q") or None)})
            if path == "/api/brief.md":
                return self._send(HTTPStatus.OK, brief.markdown(c, c["written"]).encode(), "text/markdown; charset=utf-8")
            name = threads.slug(args.get("id") or args.get("q") or "")
            thread = self.state.thread(name, at) if threads.load(self.vault_path, name) else None
            return self._json(HTTPStatus.OK, {**c, "thread": thread and {k: v for k, v in thread.items() if k != "context"}})
        if path == "/api/brief/entity":
            r = self.state.brief_entity(args.get("id") or None, args.get("q") or None, args.get("entity", ""))
            return self._json(HTTPStatus.OK if r else HTTPStatus.NOT_FOUND, r or {"error": "No such person here."})
        if match := BRIEF_SOURCE.match(path):
            r = self.state.brief_source(args.get("id") or None, args.get("q") or None, match[1])
            return self._json(HTTPStatus.OK if r else HTTPStatus.NOT_FOUND, r or {"error": "No such source here."})
        if path == "/api/threads":
            return self._json(HTTPStatus.OK, self.state.threads())
        if match := THREAD.match(path):
            if match[2] == "snapshots" and match[3]:
                snap = next((x for x in threads.snapshots(self.vault_path, match[1]) if x["stamp"] == match[3]), None)
                return self._json(HTTPStatus.OK if snap else HTTPStatus.NOT_FOUND, snap or {"error": "No such stamp."})
            if match[2] == "snapshots":
                snaps = threads.snapshots(self.vault_path, match[1])
                diffs = [threads.diff(a, b) for a, b in zip(snaps, snaps[1:])]
                return self._json(HTTPStatus.OK, {"snapshots": [{k: v for k, v in x.items() if k != "items"} for x in snaps],
                                                  "diffs": diffs})
            v = self.state.thread(match[1])
            return self._json(HTTPStatus.OK if v else HTTPStatus.NOT_FOUND,
                              {k: x for k, x in v.items() if k != "context"} if v else {"error": "No such thread."})
        if path == "/api/dossier":
            try:
                at = datetime.fromisoformat(args["at"]) if args.get("at") else None
            except ValueError:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "at must be an ISO date."})
            result = self.state.dossier(args.get("topic") or None, args.get("q") or None,
                                   at.replace(tzinfo=at.tzinfo or timezone.utc) if at else None)
            return self._json(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                              result or {"error": "Nothing in your knowledge matches that yet."})
        if path == "/api/subject":
            s = self.state.subject(args.get("id") or None, args.get("q") or None)
            if s is None:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Nothing in your knowledge matches that yet.",
                                                         "named": self.state.named(args.get("q") or None)})
            return self._json(HTTPStatus.OK, {**s, **subject.plan(s, self.state.marks())})
        if path in ("/api/subject/results", "/api/path"):
            s = self.state.subject(args.get("id") or None, args.get("q") or None)
            if s is None:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Nothing in your knowledge matches that yet."})
            try:
                if path == "/api/path":
                    levels = subject.path(s, args.get("cluster") or None, args.get("source") or None,
                                          args.get("idea") or None)
                    return self._json(HTTPStatus.OK, {"levels": levels})
                return self._json(HTTPStatus.OK, subject.results(
                    s, subject.plan(s, self.state.marks()), args.get("group_by") or "cluster_home",
                    args.get("scope") or None, args.get("only_matches", "1") not in ("0", "false")))
            except ValueError as error:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except KeyError:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "No such cluster, source, Idea or topic here."})
        if (match := SOURCE.match(path)) and match[2] == "context":
            result = self.state.context(args.get("id") or None, args.get("q") or None, match[1])
            return self._json(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                              result or {"error": "No such source in this subject."})
        if path == "/api/document":
            result = self.state.document(args.get("id") or "")
            return self._json(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                              result or {"error": "No such source in the vault."})
