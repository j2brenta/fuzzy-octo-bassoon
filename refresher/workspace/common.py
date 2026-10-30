"""What the parts of the capture workspace share: the size a file may have, reading a job, and how a stopped
one can recover."""
import json
import logging

MAX_FILE = 20 * 1024 * 1024
logger = logging.getLogger("refresher.web")  # the name the workspace has always logged under


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def recovery(job):
    """Old receipts predate recovery codes; give them the same useful actions."""
    if job.get("status") != "pending":
        return None
    message = job.get("message", "")
    if any(limit in message for limit in ("PDF processing supports up to 20 pages", "PDF text exceeds 50,000 characters")):
        return "retry"  # long PDFs are now read up to the limits
    if job.get("recovery"):
        return job["recovery"]
    if "AI processing is disabled" in message:
        return "setup"
    if job.get("stage") == "Link needs attention":
        return "paste"
    if job.get("attachment") and any(word in message for word in ("parser", "Scanned PDF", "UTF-8 text", "Extracted text is empty")):
        return "format"
    return "retry"
