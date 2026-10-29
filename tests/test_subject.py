import json

import pytest

from graph.memory import MemoryStore
from refresher import dossier, subject, vault
from tests.test_proposals import AT, topical_vault
from tests.test_relate import item, verdicts

HALLUCINATION = {f"item-{c}" for c in ("h1", "h2", "h3", "h4", "m1", "m2")}


def scored(tmp_path, phrase="hallucination detection"):
    store = vault.replay(topical_vault(tmp_path), include_unreviewed=True)
    return store, subject.subject(store, dossier.resolve(store, query=phrase), AT)


def test_every_source_has_a_place_and_the_subject_decides_the_bands(tmp_path):
    store, s = scored(tmp_path)
    assert set(s["sources"]) == {i.item_id for i in store.items()}  # nothing vanishes
    main = next(c for c in s["clusters"] if c["band"] == "main")
    assert {main["anchor"], *main["perspectives"], *main["noise"]} == HALLUCINATION
    far = {i for c in s["clusters"] if c["band"] == "far" for i in (c["anchor"], *c["perspectives"], *c["noise"])}
    assert {"item-g1", "item-g2", "item-g3"} <= far
    assert all(s["ideas"][i]["relevance"] >= s["floor"] for src in s["sources"].values() for i in src["ideas"])
    roles = {c["anchor"]: "anchor" for c in s["clusters"]}
    assert all(s["sources"][i]["role"] == roles.get(i, s["sources"][i]["role"]) for i in s["sources"])


def test_the_plan_covers_the_subject_greedily_and_what_you_read_counts(tmp_path):
    _, s = scored(tmp_path)
    p = subject.plan(s, {})
    steps = [x["source"] for x in p["plan"]]
    assert p["coverage"] == 0 and steps[0] == next(c["anchor"] for c in s["clusters"] if c["band"] == "main")
    cumulative = [x["cumulative"] for x in p["plan"]]
    assert cumulative == sorted(cumulative) and (cumulative[-1] >= subject.TARGET or len(steps) == subject.PLAN_MAX)
    assert cumulative[-2] < subject.TARGET  # it stops once the target is reached
    assert all(p["share"][a] >= p["share"][b] for a, b in zip(steps, steps[1:]))
    read = subject.plan(s, {steps[0]: "read"})
    assert steps[0] not in [x["source"] for x in read["plan"]] and read["coverage"] == pytest.approx(cumulative[0], abs=1e-3)
    skimmed = subject.plan(s, {steps[0]: "skimmed"})
    assert skimmed["coverage"] == pytest.approx(cumulative[0] * subject.SKIM, abs=1e-3)


def _store_with_a_bridge():
    """Two clusters of the subject joined by a relation, a repost, a source without Ideas, and people."""
    store = MemoryStore()
    store.apply(item("a", 10, [("a1", "Semantic entropy detects hallucinations in language models."),
                               ("a2", "Semantic entropy needs several sampled answers per question.")],
                     author="Ann Lee", entities=[("Bo Chen", "person"), ("Oxford", "organization")]),
                "Semantic entropy detects hallucinations. Bo Chen at Oxford agrees.",
                verdicts("a", ("a1", "NONE", None), ("a2", "NONE", None)))
    store.apply(item("b", 11, [("b1", "Probes on hidden states detect hallucinations cheaply."),
                               ("b2", "Hidden state probes run in a single forward pass.")], author="Bo Chen"), "Probes detect them cheaply.",
                verdicts("b", ("b1", "ADDS_DETAIL", "a1"), ("b2", "NONE", None)))
    store.apply(item("c", 12, [("c1", "Semantic entropy detects hallucinations in language models.")], author="Cy Ito"),
                "Forwarded: semantic entropy detects hallucinations.", verdicts("c", ("c1", "SAME", "a1")))
    store.item("c").duplicate_of = "a"
    store.apply(item("d", 13, [], author="Di Roy"), "", verdicts("d"))
    return store


def test_roles_edges_and_context():
    store = _store_with_a_bridge()
    scope = dossier.resolve(store, query="detect hallucinations")
    s = subject.subject(store, scope, AT)
    assert s["sources"]["c"]["role"] == "noise"  # a repost adds nothing
    assert s["sources"]["d"]["role"] == "noise" and s["no_ideas"] == ["d"]
    homes = {s["sources"][i]["cluster"] for i in ("a", "b")}
    if len(homes) == 2:  # the relation between a1 and b1 is the edge between their clusters
        assert [(e["count"], s["ideas"][e["label"]]["title"]) for e in s["edges"]][0][0] >= 1
    a = next(x for x in s["sources"]["b"]["shares"] if x["source"] == "a")
    assert s["ideas"][a["idea"]]["in_scope"]
    ctx = subject.context(store, s, "a", {"b": "read"})
    assert ctx["text"] and {p["name"]: p["relation"] for p in ctx["people"]} == {"Ann Lee": "wrote this", "Bo Chen": "mentioned here"}
    assert next(p for p in ctx["people"] if p["name"] == "Bo Chen")["elsewhere"] == 1
    assert [o["relation"] for o in ctx["orgs"]] == ["mentioned here"]
    assert {c["source"] for c in ctx["connected"]} >= {"b", "c"}
    assert all(i["passages"] for i in ctx["ideas"]) and any(i["new"] for i in ctx["ideas"])
    assert subject.context(store, s, "nope", {}) is None


def test_the_app_serves_a_subject_and_takes_reading_state(tmp_path):
    from tests.test_app import get, send
    from refresher import app
    from refresher.web import Workspace
    import threading
    from http.server import ThreadingHTTPServer
    v = topical_vault(tmp_path)
    workspace = Workspace(v, place=lambda item_id, client: {"item": item_id, "status": "placed"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler(v, workspace, app.State(v), log=lambda m: None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        s = json.loads(get(base + "/api/subject?q=hallucination+detection")[1])
        first = s["plan"][0]["source"]
        assert s["coverage"] == 0 and len(s["sources"]) == 10 and s["clusters"][0]["band"] == "main"
        ctx = json.loads(get(f"{base}/api/source/{first}/context?q=hallucination+detection")[1])
        assert ctx["id"] == first and ctx["ideas"] and "connected" in ctx
        status, after = send(base, f"/api/source/{first}/state", {"state": "read", "q": "hallucination detection"})
        assert status == 200 and after["coverage"] > 0 and first not in [x["source"] for x in after["plan"]]
        assert send(base, f"/api/source/{first}/state", {"state": "loved", "q": "hallucination detection"})[0] == 400
        assert send(base, "/api/source/nope/state", {"state": "read", "q": "hallucination detection"})[0] == 404
        assert send(base, f"/api/source/{first}/state", {"state": "read", "q": "hallucination detection"},
                    {"X-Refresher": "0"})[0] == 403
        again = json.loads(get(base + "/api/subject?q=hallucination+detection")[1])
        assert again["marks"] == {first: "read"} and again["coverage"] == after["coverage"]
    finally:
        server.shutdown()
        server.server_close()
        workspace.worker.shutdown(wait=True)
