from refresher import context, profile, thesis_relate
from tests.test_context import AT, dense  # noqa: F401 - the fixture


def card(store, eid, query="semantic entropy confabulations"):
    pool = thesis_relate.Pool(store)
    c = context.build(store, query=query, at=AT, pool=pool)
    return profile.card(store, pool, c, eid, AT)


def test_a_ranked_writer_shows_their_place_and_who_credits_them(dense):  # noqa: F811
    ann = card(dense, "person:ann-lee")
    assert ann["kind"] == "person" and ann["standing"]["rank"] == 1 and "1 base" in ann["standing"]["why"]
    assert ann["sources"][0] == {**ann["sources"][0], "item": "paper", "relation": "wrote", "in_brief": True}
    bo = next(t for t in ann["people"] if t["id"] == "person:bo-chen")
    assert "links to them" in bo["how"] and bo["rank"] == 2  # Bo Chen's blog links the paper


def test_one_credit_ranks_a_person_named_once_and_many_mentions_do_not(dense):  # noqa: F811
    fay = card(dense, "person:fay-lin")
    assert fay["standing"]["rank"] and fay["standing"]["reach"] == 1  # named once, credited by a builder
    assert any("credits them" in t["how"] for t in fay["people"] if t["id"] == "person:bo-chen")
    rex = card(dense, "person:rex-vale")
    assert rex["standing"]["rank"] is None and "credited by no one" in rex["standing"]["note"]
    assert {s["relation"] for s in rex["sources"]} == {"mentioned"}
    assert {t["id"] for t in rex["people"]} >= {"person:ann-lee"}  # met in the paper they are named in


def test_an_unknown_entity_has_no_card(dense):  # noqa: F811
    assert card(dense, "person:nobody") is None
