from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from graph.store import Edge
from refresher import dossier, navigator, subject
from tests.test_proposals import AT
from tests.test_subject import _store_with_a_bridge

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def items(*rows):
    """(id, day, words, author, duplicate_of, url) -> items shaped like ItemNode."""
    return {i: NS(item_id=i, text=" ".join(["word"] * words), author_id=author, duplicate_of=dup, linked_from=None,
                  source=NS(event_time=T0 + timedelta(days=day), url=url))
            for i, day, words, author, dup, url in rows}


def store(edges=(), people=(), experts=(), extras=None):
    return NS(edges=lambda: list(edges), people=lambda: [NS(person_id=p, name=n) for p, n in people],
              organizations=lambda: [], experts=lambda *a, **k: list(experts), extras=extras or {})


def claim(cid, item, idea, statement="a claim", kind=None):
    return NS(claim=NS(claim_id=cid, statement=statement), item_id=item, idea_id=idea,
              attribution=NS(kind=kind) if kind else None)


def test_depth_spreads_over_five_levels_and_a_repost_is_one():
    its = items(*[(f"s{n}", n, 100 * (n + 1), None, None, None) for n in range(9)], ("copy", 20, 900, None, "s8", None))
    ideas_of = {f"s{n}": {f"i{n}-{k}" for k in range(n + 1)} for n in range(9)} | {"copy": {"i8-0"}}
    claims = [claim(f"c{n}", f"s{n}", f"i{n}-0", "costs 3 dollars" if n > 4 else "plain") for n in range(9)]
    d = navigator.depth(store(), its, ideas_of, claims, {"copy"})
    assert {x["depth"] for i, x in d.items() if i != "copy"} == {1, 2, 3, 4, 5}
    assert d["s0"]["depth"] == 1 and d["s8"]["depth"] == 5 and d["copy"]["depth"] == 1
    assert d["s8"]["inputs"] == {"words": 900, "ideas": 9, "specific": 1}


def test_trust_is_a_word_from_the_people_scoring_and_unscored_reads_unknown():
    rows = [{"person_id": "p:ann", "importance": 0.4, "track_record": 0.75, "evidence_level": "developing"},
            {"person_id": "p:bo", "importance": 0.2, "track_record": 0.75, "evidence_level": "limited"},
            {"person_id": "p:cy", "importance": None, "track_record": 0.5, "evidence_level": "limited"}]
    its = items(("a", 1, 9, "p:ann", None, None), ("b", 2, 9, "p:bo", None, None), ("c", 3, 9, "p:cy", None, None),
                ("d", 4, 9, None, None, None))
    t = navigator.trust(store(experts=rows), its, AT, ["i1"], "subject")
    assert {i: x["trust"] for i, x in t.items()} == {"a": "trusted", "b": "known", "c": "unknown", "d": "unknown"}
    assert t["a"]["trust_score"] == 0.75 and t["a"]["trust_basis"] == "author"
    assert t["c"] == {"trust": "unknown", "trust_score": None, "trust_basis": "none"}


def test_the_real_people_scoring_leaves_unattributed_writers_unknown():
    store_ = _store_with_a_bridge()
    s = subject.subject(store_, dossier.resolve(store_, query="detect hallucinations"), AT)
    assert {x["trust"] for x in s["sources"].values()} == {"unknown"}  # canned verdicts carry no attribution


def test_stance_follows_attribution_then_who_said_it_first():
    its = items(("early", 1, 9, None, None, None), ("late", 2, 9, None, None, None), ("quote", 3, 9, None, None, None))
    ideas_of = {"early": {"x"}, "late": {"y"}, "quote": {"z"}}
    claims = [claim("c1", "early", "x"), claim("c2", "late", "y"), claim("c3", "quote", "z", kind="quoted")]
    st = navigator.stances(store(), its, claims, ideas_of, {("x", "y"), ("y", "x")}, {i: x.source.event_time for i, x in its.items()})
    assert st == {("early", "x"): "states", ("late", "y"): "discusses", ("quote", "z"): "discusses"}
    claims[1].attribution = NS(kind="author")  # the source's own writer asserts it, whoever said it before
    st = navigator.stances(store(), its, claims, ideas_of, {("x", "y"), ("y", "x")}, {i: x.source.event_time for i, x in its.items()})
    assert st[("late", "y")] == "states"


def placed(**overrides):
    """Three clusters: A shared by p (big), q and a repost of p; B shared by p and q; C only p's."""
    cluster_of = {"a1": "A", "a2": "A", "a3": "A", "b1": "B", "b2": "B", "c1": "C", "c2": "C", "c3": "C"}
    ideas_of = {"p": {"a1", "a2", "b1", "c1", "c2", "c3"}, "q": {"a3", "b1", "b2"}, "r": {"a1", "a2"}}
    when = {"p": T0, "q": T0 + timedelta(days=1), "r": T0 + timedelta(days=2)}
    args = dict(cluster_of=cluster_of, ideas_of=ideas_of, weight={x: 1.0 for x in cluster_of},
                relevance={x: 0.5 for x in cluster_of}, stance={(i, x): "states" for i in ideas_of for x in ideas_of[i]},
                deep={i: {"depth": 3} for i in ideas_of}, trusted={i: {"trust": "unknown"} for i in ideas_of},
                when=when, reposts={"r"}, originals={"r": "p"}, first_passage={("p", "a2"): [5, 9], ("p", "a1"): [40, 50]})
    return navigator.placements(**{**args, **overrides})


def test_every_cluster_has_one_anchor_never_a_repost_and_every_source_one_home():
    rows, home = placed()
    for cid, places in rows.items():
        assert [r["role"] for r in places].count("anchor") == 1
        assert all(r["role"] == "noise" for r in places if r["source"] == "r")
    assert home == {"p": "A", "q": "B", "r": "A"}  # C is p's alone, so A (shared, anchor) is home; r follows p
    assert sum(r["home"] for places in rows.values() for r in places) == 3
    a = {r["source"]: r for r in rows["A"]}
    assert a["p"]["role"] == "anchor" and a["q"]["role"] == "perspective" and a["q"]["new_ideas"] == ["a3"]
    assert a["p"]["passage"] == [5, 9]  # its first Idea here in text order


def test_stance_comes_before_coverage_and_roles_follow_the_subject():
    stance = {(i, x): "states" for i, xs in {"p": {"a1", "a2", "b1", "c1", "c2", "c3"}, "q": {"a3", "b1", "b2"},
                                             "r": {"a1", "a2"}}.items() for x in xs}
    stance |= {("q", "b1"): "states", ("q", "b2"): "states", ("p", "b1"): "discusses"}
    rows, _ = placed(stance=stance)
    assert next(r for r in rows["B"] if r["role"] == "anchor")["source"] == "q"
    # Only b1 in scope: in B both cover it, q states and p discusses, so q anchors and p adds nothing.
    rows, _ = placed(stance=stance, weight={"b1": 0.9})
    b = {r["source"]: r for r in rows["B"]}
    assert (b["q"]["role"], b["p"]["role"], b["p"]["cluster_role"]) == ("anchor", "noise", "noise")
    a = {r["source"]: r["role"] for r in rows["A"]}  # nothing of A in scope: the cluster on its own decides
    assert a == {"p": "anchor", "q": "perspective", "r": "noise"}


def test_links_are_typed_from_later_to_earlier_with_one_type_per_pair():
    its = items(("paper", 5, 9, "p:kim", None, "https://arxiv.org/abs/1"), ("note", 1, 9, None, None, None),
                ("copy", 6, 9, "p:kim", "paper", None), ("reply", 7, 9, None, None, None), ("other", 8, 9, "p:lee", None, None))
    ideas_of = {"paper": {"x"}, "note": {"y"}, "copy": {"x"}, "reply": {"z"}, "other": {"w"}}
    cluster_of = {"x": "A", "y": "A", "z": "B", "w": "C"}
    claims = [claim("cp", "paper", "x"), claim("cn", "note", "y"), claim("cc", "copy", "x"), claim("cr", "reply", "z"),
              claim("co", "other", "w")]
    edges = [Edge("cr", "CONTRADICTS", "cp", "says the opposite"), Edge("note", "MENTIONS", "p:kim")]
    s = store(edges, people=[("p:kim", "Kim Krestnikov"), ("p:lee", "Lee")],
              extras={"note": {"links": ["http://www.arxiv.org/abs/1/"]}})
    when = {i: x.source.event_time for i, x in its.items()}
    out = {(r["from"], r["to"]): r for r in navigator.links(s, its, claims, ideas_of, cluster_of, set(), when, lambda i: i)}
    # The note links the paper and shares its cluster: based on, from the note, though the paper is dated later.
    assert out[("note", "paper")]["type"] == "based_on" and out[("note", "paper")]["label"].startswith("links it")
    assert out[("copy", "paper")]["type"] == "retells" and "same_author" in out[("copy", "paper")]["also"]
    assert out[("reply", "paper")]["type"] == "disputes" and out[("reply", "paper")]["label"] == "says the opposite"
    assert ("other", "paper") not in out and ("paper", "other") not in out
    assert all(when[r["from"]] > when[r["to"]] for (a, b), r in out.items() if r["type"] not in ("based_on", "cites"))


def test_a_name_across_scripts_counts_as_naming_the_author():
    assert navigator._surnames("Кости Крестникова") & navigator._surnames("Konstantin Krestnikov")
    assert not navigator._surnames("Иван Петров") & navigator._surnames("Konstantin Krestnikov")


@pytest.fixture
def bridged():
    store_ = _store_with_a_bridge()
    s = subject.subject(store_, dossier.resolve(store_, query="detect hallucinations"), AT)
    return store_, s


def test_the_subject_carries_placements_signals_and_links(bridged):
    store_, s = bridged
    with_ideas = [i for i in s["sources"] if i not in s["no_ideas"]]
    assert all(sum(p["home"] for p in s["sources"][i]["places"]) == 1 for i in with_ideas)
    assert all(not s["sources"][i]["places"] and s["sources"][i]["cluster"] is None for i in s["no_ideas"])
    for c in s["clusters"]:
        assert [r["role"] for r in c["placements"]].count("anchor") == 1
        assert not s["sources"][c["anchor"]]["repost_of"]
    assert all(1 <= x["depth"] <= 5 and x["trust"] in navigator.TRUST_RANK for x in s["sources"].values())
    assert s["sources"]["c"]["depth"] == 1  # a repost
    kinds = {(r["from"], r["to"]): r["type"] for r in s["links"]}
    assert kinds[("c", "a")] == "retells"


def test_reading_context_folds_retellings_and_says_which_way_a_link_points(bridged):
    store_, s = bridged
    ctx = subject.context(store_, s, "a", {})
    assert ctx["retold_by"] == ["c"] and "c" not in {r["source"] for r in ctx["links"]}
    b = next(r for r in ctx["links"] if r["source"] == "b")
    assert b["direction"] == "later" and b["word"] and b["label"] and isinstance(b["deeper"], bool)
    assert all(i["role"] in navigator.ROLE_RANK and i["stance"] in ("states", "discusses") for i in ctx["ideas"] if i["cluster"])
    assert ctx["placements"] and all(p["source"] == "a" for p in ctx["placements"])
    from_copy = subject.context(store_, s, "c", {})
    assert [(r["source"], r["type"]) for r in from_copy["links"] if r["source"] == "a"] == [("a", "retells")]


@pytest.mark.parametrize("group_by", subject.GROUPS)
def test_results_list_every_source_once_per_row_and_the_slices_add_up(bridged, group_by):
    _, s = bridged
    p = subject.plan(s, {})
    r = subject.results(s, p, group_by)
    matching = {i for i, x in s["sources"].items() if x["ideas"]}
    assert r["header"]["matching"] == len(matching) and {x["source"] for x in r["rows"]} == matching
    assert sum(x["count"] for x in r["slices"]) == len(r["rows"])
    assert all(sum(x["roles"].values()) == x["count"] for x in r["slices"])
    groups = [x["group"] for x in r["rows"]]
    assert groups == sorted(groups, key=[x["group"] for x in r["slices"]].index)  # rows come group by group
    if group_by == "cluster_all":
        assert len(r["rows"]) == r["header"]["placements"] == sum(len(x["places"]) for x in s["sources"].values() if x["ideas"])
        assert all(x["placement"]["cluster"] == x["group"] for x in r["rows"])
    else:
        assert len(r["rows"]) == len(matching)
    everything = subject.results(s, p, group_by, only_matches=False)
    assert {x["source"] for x in everything["rows"]} == set(s["sources"])
    first_unmatched = next((n for n, x in enumerate(everything["rows"]) if not x["match"]), None)
    if first_unmatched is not None and group_by == "role":
        assert all(x["match"] for x in everything["rows"][:first_unmatched] if x["group"] == everything["rows"][first_unmatched]["group"])


def test_results_reject_an_unknown_grouping(bridged):
    _, s = bridged
    with pytest.raises(ValueError):
        subject.results(s, subject.plan(s, {}), "colour")


def test_the_path_names_each_level_with_its_position(bridged):
    _, s = bridged
    x = s["sources"]["a"]
    idea = x["all_ideas"][-1]
    levels = subject.path(s, source="a", idea=idea)
    assert [lv["level"] for lv in levels] == ["all", "subject", "cluster", "source", "idea"]
    cluster = levels[2]
    assert cluster["id"] == x["cluster"] and 1 <= cluster["position"] <= cluster["of"] == len(s["clusters"])
    assert 1 <= levels[3]["position"] <= levels[3]["of"] and 1 <= levels[4]["position"] <= levels[4]["of"]
    assert subject.path(s)[-1]["level"] == "subject"
    with pytest.raises(KeyError):
        subject.path(s, source="nope")


def test_the_app_serves_results_and_the_path(tmp_path):
    from tests.test_app import get
    from tests.test_proposals import topical_vault
    from refresher import app
    from refresher.web import Workspace
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    v = topical_vault(tmp_path)
    workspace = Workspace(v, place=lambda item_id, client: {"item": item_id, "status": "placed"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler(v, workspace, app.State(v), log=lambda m: None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        r = json.loads(get(base + "/api/subject/results?q=hallucination+detection&group_by=cluster_all")[1])
        assert r["header"]["rows"] == len(r["rows"]) and r["slices"]
        first = r["rows"][0]["source"]
        levels = json.loads(get(f"{base}/api/path?q=hallucination+detection&source={first}")[1])["levels"]
        assert levels[-1]["id"] == first
        for bad, code in (("/api/subject/results?q=hallucination+detection&group_by=colour", 400),
                          ("/api/path?q=hallucination+detection&source=nope", 404),
                          ("/api/subject/results?q=zzzz+qqqq", 404)):
            with pytest.raises(HTTPError) as error:
                get(base + bad)
            assert error.value.code == code
    finally:
        server.shutdown()
        server.server_close()
        workspace.worker.shutdown(wait=True)



def test_the_reading_layout_is_the_atlas_with_a_gist_for_every_source(tmp_path):
    from tests.test_app import get
    from tests.test_proposals import topical_vault
    from refresher import app
    from refresher.web import Workspace
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen
    v = topical_vault(tmp_path)
    workspace = Workspace(v, place=lambda item_id, client: {"item": item_id, "status": "placed"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler(v, workspace, app.State(v), log=lambda m: None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + "/read/?topic=x") as response:
            assert response.geturl() == base + "/atlas/?layout=read&topic=x"
            assert 'id="reader"' in response.read().decode()
        rows = json.loads(get(base + "/api/data")[1])["items"]
        assert rows and all(r["summary"] in ("model", "lead") and (r["gist"] or r["hook"]) for r in rows)
    finally:
        server.shutdown()
        server.server_close()
        workspace.worker.shutdown(wait=True)


class _Unreachable:
    """The configured embedder, except that a text it has not seen times out, as Nebius did on 29 October."""

    def __init__(self, inner, known):
        self.inner, self.known, self.name, self.thresholds = inner, set(known), "unreachable", inner.thresholds

    def embed(self, texts):
        from engine.llm import ModelError
        if set(texts) - self.known:
            raise ModelError("Could not reach Nebius within the request timeout.")
        return self.inner.embed(texts)


@pytest.fixture
def unreachable():
    from graph import embed
    before = embed.current()
    store_ = _store_with_a_bridge()
    embed.use(_Unreachable(before, [i.title for i in store_.ideas()]))
    yield store_
    embed.use(before)


def test_a_phrase_the_embedder_cannot_read_is_matched_by_its_words(unreachable):
    scope = dossier.resolve(unreachable, query="hallucinations semantic")
    assert scope.kind == "query" and scope.ideas and scope.note.startswith("Matched by its words")
    s = subject.subject(unreachable, scope, AT)
    assert s["scope"]["note"] == scope.note and any(x["ideas"] for x in s["sources"].values())


def test_a_phrase_with_no_word_in_the_vault_says_why_instead_of_hanging(unreachable):
    from engine.llm import ModelError
    with pytest.raises(ModelError, match="no Idea in your knowledge holds a word"):
        dossier.resolve(unreachable, query="layoffs zzzz")


def test_the_app_answers_503_when_the_embedder_does_not(tmp_path):
    from graph import embed
    from tests.test_app import get
    from tests.test_proposals import topical_vault
    from refresher import app, vault as vault_mod
    from refresher.web import Workspace
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    v = topical_vault(tmp_path)
    before = embed.current()
    embed.use(_Unreachable(before, [i.title for i in vault_mod.replay(v, include_unreviewed=True).ideas()]))
    workspace = Workspace(v, place=lambda item_id, client: {"item": item_id, "status": "placed"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler(v, workspace, app.State(v), log=lambda m: None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(HTTPError) as error:
            get(base + "/api/subject?q=layoffs+zzzz")
        assert error.value.code == 503 and "Nebius" in json.loads(error.value.read())["error"]
        s = json.loads(get(base + "/api/subject?q=hallucination+detection")[1])  # its words are in the vault
        assert s["scope"]["note"].startswith("Matched by its words") and s["plan"]
    finally:
        embed.use(before)
        server.shutdown()
        server.server_close()
        workspace.worker.shutdown(wait=True)
