"""POST: every change the page sends, and the wiki rebuilt after a decision."""
import json
import logging
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit

from engine.llm import ModelError
from refresher import brief, graphview, people_backfill, people_review, reading, subject, threads, vault
from refresher.serving.routes import DECIDE, MAX_DECISION, MAX_REQUEST, RETRY, SOURCE, THREAD


def rebuild(vault_path: Path, include_unreviewed: bool) -> None:
    """Refresh wiki/ after a decision, as `refresher rebuild` would."""
    at = datetime.now(timezone.utc)
    with vault.lock(vault_path):
        store = vault.replay(vault_path, include_unreviewed=include_unreviewed)
        vault.export(store, vault_path, at)
        graphview.write(store, vault_path, at)

logger = logging.getLogger("refresher.app")


class Posts:
    """Every change the page sends. Taken only from the app itself: a local Host, the X-Refresher header and,
    when the browser sends one, a same-origin Origin; behind the login when one is set."""

    def do_POST(self):
        if not self._from_app():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "Changes are taken only from the app itself."})
        if not self._authorized():
            return self._ask_login()
        path = urlsplit(self.path).path
        if path == "/api/consolidate":  # no body: the button only says "now"
            started = self.review.start()
            return self._json(HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
                              {**self.review.status(), **({} if started else {"error": "A review is already running."})})
        decide, retry, source = DECIDE.match(path), RETRY.match(path), SOURCE.match(path)
        if source and source[2] != "state":
            source = None
        people_change = path in {'/api/people/attribution', '/api/people/backfill'}
        thread_post = THREAD.match(path)
        if path in ("/api/threads", "/api/brief/write") or (thread_post and thread_post[2] == "stamp" and not thread_post[3]):
            return self._brief_post(path, thread_post)
        if not (decide or retry or source or people_change or path in ("/api/submissions", "/api/reading")):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Use JSON."})
        if retry:
            try:
                return self._json(HTTPStatus.ACCEPTED, self.workspace.retry(retry[1]))
            except ValueError as error:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json(HTTPStatus.BAD_REQUEST, {'error':'Invalid content length.'})
        if not 0 < length <= (MAX_DECISION if decide or people_change else MAX_REQUEST):
            return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length else HTTPStatus.BAD_REQUEST,
                              {"error": "Send a JSON body; uploads go up to 20 MB."})
        if people_change:
            return self._people_change(path, length)
        if decide:
            return self._decide(decide[1], length)
        if path == "/api/reading":
            return self._reading(length)
        if source:
            return self._source_state(source[1], length)
        try:
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Submission must be an object.")
            return self._json(HTTPStatus.ACCEPTED, self.workspace.submit(data))
        except (ValueError, UnicodeError) as error:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except OSError:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                              {"error": "Could not save this submission. Check server storage and try again."})

    def _people_change(self, path: str, length: int):
        """A speaker review saved (and the wiki rebuilt), or a paid people update started."""
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body,dict):
                raise ValueError('Send a JSON object.')
            if path == '/api/people/attribution':
                if not isinstance(body.get('claim_id'),str) or len(body['claim_id']) > 180:
                    raise ValueError('A claim_id is required.')
                result = people_review.save(self.vault_path,body['claim_id'],body)
                rebuild(self.vault_path, self.state.include_unreviewed)
                return self._json(HTTPStatus.OK,result)
            return self._json(HTTPStatus.ACCEPTED,people_backfill.start(self.vault_path,body,self.workspace.worker))
        except vault.StaleToken as error:
            return self._json(HTTPStatus.CONFLICT, {'error':str(error)})
        except KeyError:
            return self._json(HTTPStatus.NOT_FOUND, {'error':'Claim not found.'})
        except (ValueError,TypeError,UnicodeError) as error:
            return self._json(HTTPStatus.BAD_REQUEST, {'error':str(error)})
        except OSError:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {'error':'Could not save this change. Check server storage.'})

    def _brief_post(self, path, thread_post):
        """Follow a subject, write its brief with the model, or stamp a thread. Writing calls the model, so
        these answer when the paragraphs are written (a minute for a large brief)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if 0 < length <= MAX_DECISION else {}
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "Send a JSON object."})
        key, query = (body.get("id") or None), (body.get("q") or None)
        for v in (key, query):
            if v is not None and (not isinstance(v, str) or len(v) > 200):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "id and q are short strings."})
        try:
            model = brief.client_from_environment()
        except ModelError as error:
            return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
        if path == "/api/threads":
            if not (key or query):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "Say what to follow: id or q."})
            with vault.lock(self.vault_path):
                t = threads.follow(self.vault_path, query=query, key=key, title=body.get("title") if isinstance(body.get("title"), str) else None)
            return self._json(HTTPStatus.OK, t)
        if path == "/api/brief/write":
            c = self.state.brief(key, query)
            if c is None:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Nothing in your knowledge matches that yet."})
            written = brief.write(self.vault_path, {k: v for k, v in c.items() if k != "written"}, model)
            return self._json(HTTPStatus.OK, written)
        name = thread_post[1]
        if threads.load(self.vault_path, name) is None:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No such thread."})
        try:
            with vault.lock(self.vault_path):
                with self.state._lock:
                    snap = threads.stamp(self.vault_path, self.state.store(), name, model=model, pool=self.state.pool())
        except LookupError as error:
            return self._json(HTTPStatus.NOT_FOUND, {"error": str(error)})
        return self._json(HTTPStatus.OK, {k: v for k, v in snap.items() if k != "items"})

    def _reading(self, length: int):
        """{"item": id, "state": "read" | "skimmed" | "unread"} marks a source; {"visit": scope id} notes a visit."""
        if length > MAX_DECISION:
            return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Too long."})
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Send an object.")
            at = datetime.now(timezone.utc)
            if isinstance(body.get("visit"), str):
                reading.visit(self.vault_path, body["visit"], at)
            elif isinstance(body.get("item"), str) and isinstance(body.get("state"), str):
                reading.mark(self.vault_path, body["item"], body["state"], at)
            else:
                raise ValueError("Send item and state, or visit.")
        except (ValueError, UnicodeError) as error:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        return self._json(HTTPStatus.OK, {"ok": True, "at": at.isoformat()})

    def _source_state(self, item_id: str, length: int):
        """{"state": "read" | "skimmed" | "unread", "id" or "q": the subject} marks a source and returns the plan
        and coverage recomputed for that subject."""
        if length > MAX_DECISION:
            return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Too long."})
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or not isinstance(body.get("state"), str):
                raise ValueError("Send a state: read, skimmed or unread.")
            s = self.state.subject(body.get("id") or None, body.get("q") or None)
            if s is None or item_id not in s["sources"]:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "No such source in this subject."})
            reading.mark(self.vault_path, item_id, body["state"], datetime.now(timezone.utc))
        except (ValueError, UnicodeError) as error:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        return self._json(HTTPStatus.OK, subject.plan(s, self.state.marks()))

    def _decide(self, proposal_id: str, length: int):
        try:
            body = json.loads(self.rfile.read(length))
            action, token = body["action"], body["token"]
            rationale = str(body.get("rationale") or "")[:2000]
            if not isinstance(action, str) or not isinstance(token, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "The body needs action and token."})
        try:
            decision = vault.decide(self.vault_path, proposal_id, action, token, rationale)
        except KeyError:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "No such proposal; the review may have replaced it."})
        except vault.StaleToken as error:
            return self._json(HTTPStatus.CONFLICT, {"error": str(error)})
        except ValueError as error:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        rebuild(self.vault_path, self.state.include_unreviewed)
        vault.log(self.vault_path, decision.at, f"decide: {decision.action} {decision.proposal_id}")
        return self._json(HTTPStatus.OK, {"ok": True, "action": decision.action})
