"""Processing a submission on the worker: ingestion stage by stage, then its place in the graph."""
import os
import time

from refresher.workspace.common import logger, read_json


class Processing:
    """process() runs on the one worker thread, so vault writers stay serialized."""

    def process(self, job):
        from channels.web import fetch_linked_articles
        from engine.parse import parse_capture
        from engine.llm import client_from_environment, judge_client_from_environment
        from engine.triage import triage_capture
        from engine.extract import extract_capture
        from engine.quality import quality_capture
        from engine.entities import EntityError, entity_client_from_environment, extract_entities, review_entities
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
                job.update(status="pending", stage="Ready for analysis", recovery="setup", message="Original saved. AI processing is disabled in the server configuration.")
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
                        try:
                            entities = extract_entities(path, entity_client)
                            review_entities(path, entity_client, entities)
                        except EntityError as error:
                            # People are an extra; the claims are checked and still get placed.
                            logger.warning("submission %s · item %s: people left pending: %s", job["id"][:10],
                                           path.stem[:10], error)
                            job.setdefault("entities_pending", []).append(path.stem)
                stage("Saving results")
                packaged = build_extraction(path)
                apply_storage_stub(packaged.path)
                write_item_note(path)
                if self.place is not None:
                    stage("Placing it among your Ideas")
                    job.setdefault("placed", []).append(self.place(packaged.path.stem, client))
                    self.save(job)
            failed = [row for row in links if row.get("status") != "captured"]
            logger.info("submission %s · finished in %.1f s: %d items, %d links unread", job["id"][:10],
                        time.monotonic() - started, len(paths), len(failed))
            message = "Some links could not be read. See their status below." if failed else "Your original and available results are saved."
            job.update(status="complete", stage="Processing finished", message=" ".join(filter(None, [job.get("cut"), message])))
            if job["kind"] == "link" and not any(row.get("status") == "captured" for row in links):
                job.update(status="pending", stage="Link needs attention", recovery="paste", message="The URL is saved, but article text was not available. Paste the article text to continue.")
        except Exception as error:
            logger.exception("Submission %s stopped", job["id"])
            # Do not expose API payloads, credentials or filesystem internals.
            from engine.llm import ModelError
            message = str(error) if isinstance(error, ModelError) or type(error) is ValueError else "Processing stopped. The original is retained; check the server log for details."
            job.update(status="pending", stage="Needs attention", message=message,
                       recovery="format" if type(error) is ValueError and job.get("attachment") and job.get("stage") == "Preparing source" else "retry")
        finally:
            self.save(job)
