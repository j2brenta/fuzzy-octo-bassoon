"""The capture workspace behind /capture/: common.py (shared helpers), intake.py (taking a submission in) and
processing.py (ingesting it on the worker). refresher/web.py serves it on its own, for old commands."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from engine.artifacts import atomic_json
from refresher.workspace.common import MAX_FILE, read_json, recovery  # noqa: F401
from refresher.workspace.intake import Intake
from refresher.workspace.processing import Processing


class Workspace(Intake, Processing):
    """Submissions kept in the vault (submissions/), processed one at a time on one worker."""

    def __init__(self, vault, place=None):
        self.vault = Path(vault)
        # place(item_id, client) -> where the item went in the graph; the app passes refresher.app.placer.
        self.place = place
        # One worker keeps the existing vault writers serialized.
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.lock = Lock()

    def save(self, job):
        atomic_json(self.vault / "submissions" / f"{job['id']}.json", job)

    def list_jobs(self):
        rows = [read_json(path) for path in (self.vault / "submissions").glob("*.json")]
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)

    def detail(self, job_id):
        job = read_json(self.vault / "submissions" / f"{job_id}.json")
        if not job:
            return None
        job["recovery"] = recovery(job)
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
