"""In-memory GraphStore, replayed from processed extraction records and their relation verdicts.

Used until the Neo4j store lands and kept afterwards as the test double. Relations come from verdicts
(graph/relate.py): stored ones from relations/<item_id>.json, or lexical ones computed on the fly. Everything the
verdicts imply (relation edges, Ideas, profiles) and everything scoring decides is recomputed after each apply,
so the result does not depend on the order items arrive in.

The package: common.py (ids, names, constants), store.py (MemoryStore and its state) and one module per part
of the class (placing, decisions, resolution, grouping, profiles, reads, review)."""
from graph.memory.common import (CONFIRM_DURABILITY, RELATION_TYPES, _eponym, _title, cluster_fingerprint, org_id,
                                 person_id)
from graph.memory.store import MemoryStore

__all__ = ["CONFIRM_DURABILITY", "RELATION_TYPES", "MemoryStore", "_eponym", "_title", "cluster_fingerprint", "org_id",
           "person_id"]
