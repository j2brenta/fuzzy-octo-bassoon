"""The Context Engine on a small dense subject: a paper, a post building on it, a news digest retelling it, a repost of
the digest, a source lending one fact, a correction replacing an announcement, and something off the subject."""
import json
from datetime import datetime, timezone

import pytest

from graph import embed
from graph.memory import MemoryStore
from refresher import context, thesis_relate
from tests.test_relate import item, verdicts

AT = datetime(2026, 10, 29, tzinfo=timezone.utc)
PAPER_URL = "https://arxiv.org/abs/2401.0001"


def thesis(item_id, n, statement, claims, stance="original", cites=(), durability="reference"):
    return {"id": f"{item_id}:t{n}", "statement": statement, "claims": list(claims), "stance": stance,
            "cites": list(cites), "durability": durability}


def relation(a, b, label, adds=False, why="canned"):
    return {"thesis": a, "target": b, "label": label, "adds": adds, "rationale": why}


@pytest.fixture
def dense():
    before = embed.current()
    embed.use(embed.HashedEmbedder())
    s = MemoryStore()

    def add(item_id, day, claims, author=None, text=None, url=None, durability="reference"):
        ex = item(item_id, day, claims, author=author, durability=durability)
        if url:
            ex = ex.model_copy(update={"source": ex.source.model_copy(update={"url": url})})
        s.apply(ex, text or " ".join(c for _, c in claims), verdicts(item_id))

    add("paper", 1, [("p1", "Semantic entropy detects confabulations in language models."),
                     ("p2", "Semantic entropy samples several answers and clusters them by meaning.")],
        author="Ann Lee", url=PAPER_URL)
    add("blog", 3, [("b1", "Semantic entropy can gate chatbot answers in production."),
                    ("b2", "Gating on semantic entropy cut confabulated answers by a third.")], author="Bo Chen")
    add("digest", 4, [("d1", "Researchers found a way to detect confabulations in language models.")])
    add("repost", 5, [("r1", "Researchers found a way to detect confabulations in language models.")])
    s.item("repost").duplicate_of = "digest"
    add("lender", 6, [("l1", "Confabulations in language models are arbitrary answers semantic entropy detects."),
                      ("l2", "Vocational training teaches teenagers shift work.")], author="Cy Ito")
    add("old", 2, [("o1", "The confabulation workshop starts at 14:00 UTC.")], durability="ephemeral")
    add("fix", 7, [("f1", "The confabulation workshop starts at 16:00 UTC.")], durability="ephemeral")
    add("german", 8, [("g1", "German vocational training is a respected path for teenagers.")], author="Di Roy")
    s.extras = {"blog": {"links": [PAPER_URL]}}
    s.theses = {i: {"theses": rows} for i, rows in {
        "paper": [thesis("paper", 1, "Semantic entropy detects confabulations in language models by sampling answers.",
                         ["p1", "p2"])],
        "blog": [thesis("blog", 1, "Gating chatbot answers on semantic entropy cuts confabulations in production.",
                        ["b1", "b2"], "commentary", [{"url": PAPER_URL}])],
        "digest": [thesis("digest", 1, "Researchers detect confabulations in language models.", ["d1"], "summary")],
        "lender": [thesis("lender", 1, "Fluent wrong answers mislead users.", ["l1", "l2"], "commentary")],
        "old": [thesis("old", 1, "The confabulation workshop starts at 14:00 UTC.", ["o1"], durability="ephemeral")],
        "fix": [thesis("fix", 1, "The confabulation workshop now starts at 16:00 UTC.", ["f1"], durability="ephemeral")],
        "german": [thesis("german", 1, "German vocational training is a respected path for teenagers.", ["g1"])],
    }.items()}
    s.thesis_relations = {
        "blog": {"item_id": "blog", "relations": [relation("blog:t1", "paper:t1", "BUILDS_ON", True)]},
        "digest": {"item_id": "digest", "relations": [relation("digest:t1", "paper:t1", "SAME", False)]},
        "fix": {"item_id": "fix", "relations": [relation("fix:t1", "old:t1", "UPDATES", True, "moved to 16:00")]},
    }
    yield s
    embed.use(before)


def test_the_paper_is_the_base_and_each_source_has_its_role(dense):
    c = context.build(dense, query="semantic entropy confabulations", at=AT)
    idea = next(x for x in c["major"] if x["base"] == "paper")
    assert idea["sources"] == {"paper": "base", "blog": "builder", "digest": "rewrite", "repost": "rewrite"}
    assert idea["support"] == 2  # Ann Lee and Bo Chen: a digest and its repost add no independent writer
    assert [h["step"] for h in idea["history"]] == ["first stated", "built on", "retold"]
    assert "german" not in c["sources"]  # off the subject
    assert c["counts"]["rewrites"] >= 2


def test_a_link_makes_the_linked_source_the_base_whatever_the_dates(dense):
    dense.item("paper").source = dense.item("paper").source.model_copy(
        update={"published_at": datetime(2026, 10, 20, tzinfo=timezone.utc)})  # a paper dated after the post
    c = context.build(dense, query="semantic entropy confabulations", at=AT)
    assert next(x for x in c["major"] if "blog" in x["sources"])["base"] == "paper"


def test_a_correction_supersedes_and_says_what_it_replaced(dense):
    c = context.build(dense, query="confabulation workshop time", at=AT)
    idea = next(x for x in c["major"] + c["also"] if "fix" in x["sources"])
    assert idea["base"] == "fix" and idea["sources"]["old"] == "superseded"
    [p] = idea["previously"]
    assert p["old_item"] == "old" and p["new_item"] == "fix" and p["rationale"] == "moved to 16:00"
    assert [h["step"] for h in idea["history"]] == ["first stated", "updated"]


def test_a_source_lends_facts_to_an_idea_it_is_not_part_of(dense):
    c = context.build(dense, query="semantic entropy confabulations", at=AT)
    idea = next(x for x in c["major"] if x["base"] == "paper")
    lent = {a["item"]: [f["claim"] for f in a["facts"]] for a in idea["adjacent"]}
    assert "lender" in lent and "l2" not in lent["lender"]  # the off-subject claim stays out


def test_pointers_follow_links_cites_and_captures(dense):
    pool = thesis_relate.Pool(dense)
    assert "paper" in pool.points_at["blog"]
    assert any(k == "paper:t1" for k in pool.candidates("blog")["blog:t1"])


class Fake:
    model_name = "fake"

    def __init__(self, rows):
        self.rows, self.seen = rows, None

    def complete_json(self, system_prompt, content):
        self.seen = json.loads(content)
        return type("Reply", (), {"data": {"relations": self.rows}})()


def test_the_judge_keeps_only_shown_pairs_and_known_labels(dense):
    pool = thesis_relate.Pool(dense)
    shown = pool.candidates("blog")["blog:t1"]
    model = Fake([{"thesis": "t1", "candidate": "k1", "label": "BUILDS_ON", "adds": True, "rationale": "extends"},
                  {"thesis": "t1", "candidate": "k99", "label": "SAME"},  # not shown
                  {"thesis": "t1", "candidate": "k1", "label": "SAME"},  # the same pair again
                  {"thesis": "t1", "candidate": "k2", "label": "MAYBE"}])  # not a label
    record = thesis_relate.judge(dense, pool, "blog", model)
    assert record["candidates"] == {"blog:t1": shown} and model.seen["source"]["theses"][0]["id"] == "t1"
    first = model.seen["candidates"][0]["id"]
    assert first == "k1" and len(record["relations"]) == 1
    assert record["relations"][0] == {"thesis": "blog:t1", "target": shown[0], "label": "BUILDS_ON", "adds": True,
                                      "rationale": "extends"}


def test_without_theses_or_relations_every_source_is_its_own_idea(dense):
    dense.theses, dense.thesis_relations = {}, {}
    c = context.build(dense, query="semantic entropy confabulations", at=AT)
    assert all(len([r for r in x["sources"].values() if r == "base"]) == 1 for x in c["major"])
