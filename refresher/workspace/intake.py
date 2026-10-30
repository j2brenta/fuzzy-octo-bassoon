"""Taking a submission in: a link, pasted text or a file, kept as received before any analysis."""
import base64
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from uuid import uuid4

from store.raw import capture_text
from refresher.workspace.common import MAX_FILE, read_json, recovery


class Intake:
    """submit() stores what arrived and queues it; retry() queues a stopped one again with its original."""

    def retry(self, job_id):
        with self.lock:
            job = read_json(self.vault / "submissions" / f"{job_id}.json")
            if not job:
                raise ValueError("Submission not found.")
            if job["status"] != "pending":
                raise ValueError("Only a stopped submission can be retried.")
            if recovery(job) == "format":
                raise ValueError("This format cannot be analyzed here. Paste its text or upload a supported file.")
            # Retain the same original and paid-stage caches on retry.
            job.update(status="queued", stage="Queued to resume", message="", recovery=None)
            job["items"] = job["items"][:1]
            self.save(job)
            self.worker.submit(self.process, job)
            return job

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
