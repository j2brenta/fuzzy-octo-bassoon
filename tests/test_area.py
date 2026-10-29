from refresher import area, context, thesis_relate
from tests.test_context import AT, dense  # noqa: F401 - the fixture


def built(store, query="semantic entropy confabulations"):
    pool = thesis_relate.Pool(store)
    c = context.build(store, query=query, at=AT, pool=pool)
    return c, area.area(store, c, pool), pool


def test_every_brief_source_has_a_place_and_its_ideas_colour(dense):  # noqa: F811
    c, a, _ = built(dense)
    brief = {i for x in c["major"] for i in x["sources"]}
    assert brief <= set(a["sources"])
    for i in brief:
        assert a["sources"][i]["idea"] is not None
        assert any(i in k["sources"] or i in k["also"] for k in a["clusters"])
    assert all(k["in_brief"] for k in a["clusters"] if set(k["sources"]) & brief)


def test_a_source_without_ideas_lives_in_the_area_of_its_major_idea(dense):  # noqa: F811
    for cid in ("d1", "r1"):  # the digest and its repost: as if all they said were facts about someone
        dense._claims[cid].idea_id = None
    c, a, pool = built(dense)
    home = area.homes(dense, pool, {"digest"}, area.feeds(dense, {"digest"}), {"digest": "paper:t1"})["digest"]
    assert home.startswith("cluster:") or home == "own:paper:t1"


def test_go_further_ranks_the_base_first_hides_rewrites_and_sinks_what_you_read(dense):  # noqa: F811
    c, a, pool = built(dense)
    steps = area.next_steps(dense, c, a, pool, "blog", {})
    rows = [r for g in steps for r in g["rows"]]
    assert rows[0]["item"] == "paper" and "digest" not in {r["item"] for r in rows}
    assert any(g["rewrites"] for g in steps)
    read = area.next_steps(dense, c, a, pool, "blog", {"paper": "read"})
    assert [r for g in read for r in g["rows"]][0]["item"] != "paper"


def test_the_reader_tints_the_claims_the_brief_takes(dense):  # noqa: F811
    c, a, pool = built(dense)
    r = area.reader(dense, c, a, pool, "paper", {})
    assert set(r["marked"]) == {"p1", "p2"} and {m["kind"] for m in r["marked"].values()} == {"thesis"}
    assert r["roles"][0]["role"] == "base" and r["next"] and r["here"]["cluster"]
    lender = area.reader(dense, c, a, pool, "lender", {})
    assert {m["kind"] for m in lender["marked"].values()} <= {"lent"}
    assert area.reader(dense, c, a, pool, "nope", {}) is None
