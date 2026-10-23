"""Regenerate tests/fixtures/vault: hand-written records in the shape of ingestion's raw/ and processed/.

Claims and labels are written by hand, not by a model, so the KM and graph tests make no LLM calls.
They are replaced by frozen output of the real extractor once it merges.

    uv run python tests/fixtures/make_vault.py
"""
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
SEEDS = HERE.parents[1] / "eval" / "seed_corpus" / "raw"
VAULT = HERE / "vault"


def item_id(channel, source_id, message_id):
    return hashlib.sha256(json.dumps([channel, source_id, message_id, None]).encode()).hexdigest()


def claim(iid, n, statement, kind, durability, confidence=0.8):
    half = {"foundation": 720, "reference": 180, "news": 30, "ephemeral": 7}[durability]
    return {"claim_id": hashlib.sha256(f"{iid}:{n}:{statement}".encode()).hexdigest(), "statement": statement,
            "kind": kind, "durability_class": durability, "half_life_days": half, "confidence": confidence,
            "support_status": "quoted_not_fact_checked",
            "evidence": {"item_id": iid, "section_id": f"{iid}:section:1", "start_char": 0, "end_char": 10}}


def person(iid, name, kind="person"):
    return {"entity_id": hashlib.sha256(f"{iid}:{kind}:{name.casefold()}".encode()).hexdigest(), "name": name,
            "kind": kind, "mentions": [], "identity_status": "local_mention_not_resolved"}


SHARED = "Semantic entropy detects confabulations by sampling multiple answers."
ITEMS = [
    # (seed file or literal text, source fields, triage, claims, entities, quality passed)
    ("tg_people_to_follow.json",
     {"source_id": "seed:supplied-forwards", "message_id": "people-list", "source_name": "Supplied Telegram forwards",
      "author_hint": {"name": "AI assistant"}, "forwarded_by": "Kirill R"},
     ("post", "reference"),
     [(SHARED, "fact", "reference"),
      ("Models show a limited, context-dependent form of introspective awareness.", "fact", "reference"),
      ("METR measures how long a task agents can complete autonomously.", "fact", "reference")],
     ["Jack Lindsey", "Yarin Gal", "Sebastian Farquhar", "Hamel Husain", ("METR", "organization")], True),
    ("tg_introspection.json",
     {"source_id": "seed:supplied-forwards", "message_id": "introspection-list", "source_name": "Supplied Telegram forwards",
      "author_hint": {"name": "AI assistant"}, "forwarded_by": "Kirill R"},
     ("post", "reference"),
     [(SHARED, "fact", "reference"),
      ("Models show a limited, context dependent form of introspective awareness.", "fact", "reference")],
     ["Jack Lindsey", "Yarin Gal"], True),
    ("tg_memory_redis.json",
     {"source_id": "seed:supplied-talks", "message_id": "redis-talks", "source_name": "Supplied Redis talks"},
     ("announcement", "ephemeral"),
     [("A talk on real-time retrieval for AI agents starts at 18:30.", "announcement", "ephemeral"),
      ("A talk on building memory for AI agents starts at 19:00.", "announcement", "ephemeral")],
     ["Samuel Agbede", "Evangelos Resvanis", ("Redis", "organization")], True),
    ("linkedin_why_ai_excels_in_math.json",
     {"source_id": "seed:linkedin", "message_id": "jianfeng-liu-math", "source_name": "LinkedIn",
      "author_hint": {"name": "Jianfeng Liu"}, "url": "https://www.linkedin.com/pulse/why-ai-excels-math-fails-creative-writing-how-fix-jianfeng-liu-heeyc"},
     ("article", "reference"),
     [("Problems with verifiable answers let models converge, open-ended writing does not.", "opinion", "reference"),
      ("Writing the outline while writing the prose may let models produce long coherent novels.", "opinion", "news")],
     [], True),
    ("Synthetic: Semantic entropy detects confabulations by sampling multiple answers, as Gal's group showed.",
     {"source_id": "seed:synthetic-confirmation", "message_id": "confirm-1", "source_name": "Synthetic newsletter",
      "author_hint": {"name": "Demo Author"}, "published_at": "2026-10-12T09:00:00+00:00"},
     ("post", "reference"),
     [(SHARED, "fact", "reference")], ["Yarin Gal"], True),
    ("tg_eli5.json",
     {"source_id": "seed:rvnikita_blog", "message_id": "1899", "source_name": "Мысли Рвачева",
      "author_hint": {"name": "Nikita Rvachev", "handle": "rvnikita_blog"}},
     ("post", "news"), [], [], False),
]


def main():
    shutil.rmtree(VAULT, ignore_errors=True)
    for folder in ("raw", "processed"):
        (VAULT / folder).mkdir(parents=True)
    for seed, fields, (kind, durability), claims, entities, passed in ITEMS:
        if seed.endswith(".json"):
            raw_seed = json.loads((SEEDS / seed).read_text(encoding="utf-8"))
            text, captured = raw_seed["text"], raw_seed["captured_at"]
            fields = {"published_at": raw_seed.get("published_at"), **fields}
        else:
            text, captured = seed, "2026-10-12T09:05:00+00:00"
        iid = item_id("telegram", fields["source_id"], fields["message_id"])
        raw = {"schema_version": 1, "item_id": iid, "channel": "telegram", "captured_at": captured,
               "published_at": None, "author_hint": None, "forwarded_by": None, "url": None, "kind": "text",
               **fields, "text": text, "attachments": [],
               "content_hash": hashlib.sha256(text.encode()).hexdigest()}
        source = {k: raw.get(k) for k in ("channel", "source_id", "source_name", "message_id", "captured_at",
                                          "published_at", "author_hint", "forwarded_by", "url", "kind")}
        processed = {
            "schema_version": 1, "stage": "processed_extraction", "builder_version": "fixture",
            "contract_status": "provisional_awaiting_shared_schema", "item_id": iid,
            "source_content_hash": raw["content_hash"], "raw_path": f"raw/{iid}.json", "source": source,
            "sections": [{"section_id": f"{iid}:section:1", "kind": "text"}], "links": [],
            "claims": [claim(iid, n, *c) for n, c in enumerate(claims)],
            "entities": [person(iid, *(e if isinstance(e, tuple) else (e,))) for e in entities],
            "triage": {"keep": True, "kind": kind, "durability_class": durability, "split_digest": False,
                       "reason": "fixture"},
            "quality_status": "accepted" if passed else "needs_revision", "quality_gate_passed": passed,
            "graph_status": "not_applied", "chunks": [], "embeddings_status": "not_implemented",
        }
        for folder, record in (("raw", raw), ("processed", processed)):
            (VAULT / folder / f"{iid}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                                                        encoding="utf-8")
    print(f"wrote {len(ITEMS)} items to {VAULT}")


if __name__ == "__main__":
    main()
