import json

from graph import embed


def test_stems_meet_across_word_forms():
    assert embed.stem("hallucination") == embed.stem("hallucinate") == "hallucin"
    assert embed.similarity("Models hallucinate citations.", "Citation hallucination in models.") > 0.6
    assert embed.similarity("Models hallucinate citations.", "The workshop starts at 16:00.") < 0.2


def test_hashed_vectors_are_unit_length_and_stable():
    [a] = embed.HashedEmbedder().embed(["Semantic entropy detects confabulations."])
    [b] = embed.HashedEmbedder().embed(["Semantic entropy detects confabulations."])
    assert a == b and abs(embed.cosine(a, a) - 1) < 1e-9


def test_nebius_embedder_batches_and_caches(tmp_path, monkeypatch):
    calls = []

    def fake(api_key, base_url, path, payload, timeout):
        calls.append(payload["input"])
        return {"data": [{"index": i, "embedding": [1.0, float(len(t))]} for i, t in enumerate(payload["input"])]}
    monkeypatch.setattr("engine.llm._nebius_request", fake)
    cache = tmp_path / ".cache" / "embeddings.jsonl"
    first = embed.NebiusEmbedder("key", "m", "https://x", cache).embed(["a", "bb", "a"])
    assert calls == [["a", "bb"]] and first[0] == first[2]
    again = embed.NebiusEmbedder("key", "m", "https://x", cache).embed(["bb"])
    assert len(calls) == 1 and again[0] == first[1]
    assert all(json.loads(line)["model"] == "m" for line in cache.read_text().splitlines())


def test_without_a_model_the_offline_embedder_is_used(monkeypatch):
    monkeypatch.delenv("NEBIUS_EMBED_MODEL", raising=False)
    assert isinstance(embed.from_environment(), embed.HashedEmbedder)
