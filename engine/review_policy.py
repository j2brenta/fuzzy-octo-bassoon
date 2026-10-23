"""Correctness gates are separate from advisory completeness judgments."""

POLICY_VERSION = "correctness-v1"


def claim_status(rows):
    return "accepted" if all(row["verdict"] == "supported" for row in rows) else "needs_revision"


def entity_status(rows, entities):
    blocking = {entity["entity_id"] for entity in entities if entity["kind"] != "concept"}
    return "accepted" if all(row["verdict"] == "supported" for row in rows if row["entity_id"] in blocking) else "needs_revision"


def legacy_status(review, collection, omissions):
    return "accepted" if all(row["verdict"] == "supported" for row in review[collection]) and not review[omissions] else "needs_revision"


def compatible_stored_review(stored, checked, policy_version, collection, omissions):
    if stored == checked:
        return True
    # Reassess old, structurally valid judgments without another paid model call.
    # A forged acceptance or malformed old review is still rejected.
    return policy_version is None and stored == {**checked, "status": legacy_status(checked, collection, omissions)}
