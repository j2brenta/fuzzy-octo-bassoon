"""Local jury workspace: python -m refresher.web [--port 8080]."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
from threading import Lock
import time
from uuid import uuid4

from dotenv import load_dotenv
from engine import logs
from engine.artifacts import atomic_json
from store.raw import capture_text

MAX_FILE = 20 * 1024 * 1024
MAX_REQUEST = 29 * 1024 * 1024
STATIC = Path(__file__).with_name("static")
logger = logging.getLogger(__name__)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class Workspace:
    def __init__(self, vault):
        self.vault = Path(vault)
        # One worker keeps the existing vault writers serialized.
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.lock = Lock()

    def retry(self, job_id):
        with self.lock:
            job = read_json(self.vault / "submissions" / f"{job_id}.json")
            if not job:
                raise ValueError("Submission not found.")
            if job["status"] != "pending":
                raise ValueError("Only a stopped submission can be retried.")
            # Retain the same original and paid-stage caches on retry.
            job.update(status="queued", stage="Queued to resume", message="")
            job["items"] = job["items"][:1]
            self.save(job)
            self.worker.submit(self.process, job)
            return job

    def save(self, job):
        atomic_json(self.vault / "submissions" / f"{job['id']}.json", job)

    def submit(self, data):
        text = data.get("text", "")
        title = data.get("title", "")
        if not isinstance(text, str) or not isinstance(title, str) or len(text) > 60_000 or len(title) > 200:
            raise ValueError("Use a title up to 200 characters and text up to 60,000 characters.")
        kind = data.get("kind", "text")
        if kind not in {"text", "link", "file"}:
            raise ValueError("Choose text, link or file.")
        if kind == "link":
            from urllib.parse import urlsplit
            url = urlsplit(text.strip())
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or any(c.isspace() for c in text.strip()):
                raise ValueError("Enter one public HTTP or HTTPS article URL.")
        attachment = None
        if kind == "file":
            name = data.get("filename")
            encoded = data.get("content")
            if not isinstance(name, str) or not name or len(name) > 255 or not isinstance(encoded, str):
                raise ValueError("Choose a file to upload.")
            try:
                content = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise ValueError("The upload could not be decoded.") from None
            if not content or len(content) > MAX_FILE:
                raise ValueError("Files must be nonempty and no larger than 20 MB.")
            digest = hashlib.sha256(content).hexdigest()
            path = self.vault / "uploads" / digest
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("xb") as stream:
                    stream.write(content)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise ValueError("Stored upload failed its integrity check.")
            attachment = {"name": name.replace("\\", "/").split("/")[-1], "sha256": digest, "size": len(content)}
        elif not text.strip():
            raise ValueError("Add some content first.")
        job = {"id": uuid4().hex, "title": title.strip() or (attachment["name"] if attachment else text.strip().splitlines()[0][:100]),
               "kind": kind, "status": "queued", "stage": "Original saved" if attachment else "Queued",
               "created_at": datetime.now(timezone.utc).isoformat(), "attachment": attachment, "items": [], "message": ""}
        # Persist text before acknowledging the request or scheduling model work.
        if kind != "file":
            capture = self.capture(text, job)
            job["items"].append(capture.item_id)
        else:
            job["caption"] = text
        self.save(job)
        self.worker.submit(self.process, job)
        return job

    def capture(self, text, job):
        return capture_text(text, self.vault, channel="app", source_id=f"jury:{job['id']}",
                            source_name=job["title"], channel_metadata={"submission_id": job["id"], "attachment": job["attachment"]})

    def file_text(self, job):
        attachment = job["attachment"]
        path = self.vault / "uploads" / attachment["sha256"]
        if Path(attachment["name"]).suffix.lower() in {".txt", ".md", ".csv", ".json", ".log", ".eml"}:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeError:
                raise ValueError("Original saved. This file is not UTF-8 text.") from None
        elif path.read_bytes()[:5] == b"%PDF-":
            try:
                import pypdfium2 as pdfium
            except ImportError:
                raise ValueError("Original saved. Install the web extras to read PDFs.") from None
            document = pdfium.PdfDocument(str(path))
            try:
                if len(document) > 20:
                    raise ValueError("Original saved. PDF processing supports up to 20 pages.")
                pages = []
                for index in range(len(document)):
                    page = document[index]
                    textpage = page.get_textpage()
                    try:
                        page_text = textpage.get_text_range()
                    finally:
                        textpage.close()
                        page.close()
                    if len(page_text.strip()) < 15:
                        raise ValueError("Original saved. Scanned PDF pages need OCR; upload a text PDF or paste the transcript.")
                    pages.append(f"[PDF page {index + 1}]\n{page_text}")
                    if sum(map(len, pages)) > 50_000:
                        raise ValueError("Original saved. PDF text exceeds 50,000 characters; split the document.")
                text = "\n\n".join(pages)
            finally:
                document.close()
        else:
            raise ValueError("Original saved. This format needs a parser that is not connected in this checkout. Paste its text or transcript to analyze it.")
        if not text.strip() or len(text) > 60_000:
            raise ValueError("Original saved. Extracted text is empty or exceeds 60,000 characters.")
        return text

    def process(self, job):
        from channels.web import fetch_linked_articles
        from engine.parse import parse_capture
        from engine.llm import client_from_environment, judge_client_from_environment
        from engine.triage import triage_capture
        from engine.extract import extract_capture
        from engine.quality import quality_capture
        from engine.entities import entity_client_from_environment, extract_entities, review_entities
        from engine.package import build_extraction
        from engine.placement import apply_storage_stub
        from engine.results import write_item_note
        from engine.parse.text import text_without_links

        started = time.monotonic()

        def stage(label):
            logger.info("submission %s · %s (%.1f s in)", job["id"][:10], label, time.monotonic() - started)
            job.update(status="processing", stage=label)
            self.save(job)

        try:
            stage("Preparing source")
            if job["attachment"] and not job["items"]:
                text = self.file_text(job)
                # A sender caption is metadata, not document evidence.
                result = self.capture(text, job)
                job["items"].append(result.item_id)
                self.save(job)
            parent = self.vault / "raw" / f"{job['items'][0]}.json"
            parse_capture(parent)
            stage("Reading linked articles")
            links = fetch_linked_articles(parent) if os.environ.get("WEB_FETCH", "on").lower() == "on" else []
            job["links"] = links
            paths = [parent]
            for link in links:
                if link.get("status") == "captured":
                    job["items"].append(link["item_id"])
                    paths.append(self.vault / "raw" / f"{link['item_id']}.json")
            self.save(job)
            client = client_from_environment()
            if client is None:
                logger.info("submission %s · AI processing is off (TRIAGE_PROVIDER)", job["id"][:10])
                job.update(status="pending", stage="Ready for analysis", message="Original saved. AI processing is disabled in the server configuration.")
                return
            for path in paths:
                raw = read_json(path)
                document = read_json(self.vault / "documents" / path.name)
                if not text_without_links(raw["text"], document["links"]).strip():
                    continue
                stage("Classifying content")
                decision = triage_capture(path, client)
                logger.info("submission %s · item %s triage: %s · %s · %s", job["id"][:10], path.stem[:10],
                            "keep" if decision.labels["keep"] else "skip", decision.labels["kind"],
                            decision.labels["durability_class"])
                if not decision.labels["keep"]:
                    write_item_note(path)
                    continue
                if decision.labels["keep"]:
                    stage("Extracting claims")
                    extraction = extract_capture(path, client)
                    logger.info("submission %s · item %s: %d claims", job["id"][:10], path.stem[:10], len(extraction.claims))
                    judge = judge_client_from_environment()
                    if judge:
                        stage("Reviewing source evidence")
                        review = quality_capture(path, judge).reviewed.review
                        logger.info("submission %s · item %s quality: %s · %d/%d supported", job["id"][:10], path.stem[:10],
                                    review["status"], sum(r["verdict"] == "supported" for r in review["claims"]),
                                    len(review["claims"]))
                    entity_client = entity_client_from_environment()
                    if entity_client:
                        stage("Reading people and organizations")
                        entities = extract_entities(path, entity_client)
                        review_entities(path, entity_client, entities)
                stage("Saving results")
                packaged = build_extraction(path)
                apply_storage_stub(packaged.path)
                write_item_note(path)
            failed = [row for row in links if row.get("status") != "captured"]
            logger.info("submission %s · finished in %.1f s: %d items, %d links unread", job["id"][:10],
                        time.monotonic() - started, len(paths), len(failed))
            job.update(status="complete", stage="Processing finished", message="Some links could not be read. See their status below." if failed else "Your original and available results are saved.")
            if job["kind"] == "link" and not any(row.get("status") == "captured" for row in links):
                job.update(status="pending", stage="Link needs attention", message="The URL is saved, but article text was not available. Paste the article text to continue.")
        except Exception as error:
            logger.exception("Submission %s stopped", job["id"])
            # Do not expose API payloads, credentials or filesystem internals.
            from engine.llm import ModelError
            message = str(error) if isinstance(error, ModelError) or type(error) is ValueError else "Processing stopped. The original is retained; check the server log for details."
            job.update(status="pending", stage="Needs attention", message=message)
        finally:
            self.save(job)

    def list_jobs(self):
        rows = [read_json(path) for path in (self.vault / "submissions").glob("*.json")]
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)

    def detail(self, job_id):
        job = read_json(self.vault / "submissions" / f"{job_id}.json")
        if not job:
            return None
        job["results"] = []
        for item in job["items"]:
            raw = read_json(self.vault / "raw" / f"{item}.json")
            processed = read_json(self.vault / "processed" / f"{item}.json")
            if not processed:
                # Initial artifacts remain readable if a later API stage fails.
                extraction = read_json(self.vault / "extractions" / f"{item}.json")
                processed = {"claims": extraction.get("claims", []), "quality_status": "pending",
                             "triage": read_json(self.vault / "triage" / f"{item}.json").get("labels"),
                             "reviews": {}}
            job["results"].append({"id": item, "text": raw.get("text", ""), "source": raw.get("source_name"),
                                   "url": raw.get("url"), "processed": processed})
        return job


def handler_for(workspace):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, mime="application/json"):
            content = json.dumps(body, ensure_ascii=False).encode() if mime == "application/json" else body
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if self.path == "/api/submissions":
                return self.send(200, workspace.list_jobs())
            match = re.fullmatch(r"/api/submissions/([a-f0-9]{32})", self.path)
            if match:
                result = workspace.detail(match[1])
                return self.send(200 if result else 404, result or {"error": "Submission not found."})
            match = re.fullmatch(r"/api/uploads/([a-f0-9]{64})", self.path)
            if match:
                path = workspace.vault / "uploads" / match[1]
                if path.is_file():
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", 'attachment; filename="original-upload"')
                    self.send_header("Content-Length", str(path.stat().st_size))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(path.read_bytes())
                    return
            files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.css": ("app.css", "text/css"), "/app.js": ("app.js", "text/javascript"), "/brain.png": ("brain.png", "image/png")}
            if self.path in files:
                name, mime = files[self.path]
                return self.send(200, (STATIC / name).read_bytes(), mime)
            self.send(404, {"error": "Not found."})

        def do_POST(self):
            # Same-origin JSON only: no cross-origin form submissions to the local app.
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                return self.send(403, {"error": "Cross-origin submissions are not allowed."})
            retry = re.fullmatch(r"/api/submissions/([a-f0-9]{32})/retry", self.path)
            if self.path != "/api/submissions" and not retry:
                return self.send(404, {"error": "Not found."})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "Use JSON."})
            try:
                if retry:
                    return self.send(202, workspace.retry(retry[1]))
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST:
                    return self.send(413, {"error": "Upload exceeds the 20 MB limit."})
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Submission must be an object.")
                job = workspace.submit(data)
                self.send(202, job)
            except (ValueError, UnicodeError) as error:
                self.send(400, {"error": str(error)})
            except OSError:
                self.send(500, {"error": "Could not save this submission. Check server storage and try again."})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="bind address; the container uses 0.0.0.0 behind a loopback port")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--vault", type=Path, default=Path(".local-vault/jury"))
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    logs.configure()
    workspace = Workspace(args.vault)
    for path in (workspace.vault / "submissions").glob("*.json"):
        job = read_json(path)
        if job["status"] in {"queued", "processing"}:
            job.update(status="pending", stage="Interrupted", message="Server restarted. Original retained; submit again to process.")
            workspace.save(job)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(workspace))
    print(f"Refresher workspace: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        workspace.worker.shutdown(wait=True)


if __name__ == "__main__":
    main()
