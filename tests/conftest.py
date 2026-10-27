import pytest


@pytest.fixture(autouse=True)
def offline_embeddings(monkeypatch):
    """Tests never call the embeddings endpoint, whatever .env says: an empty variable beats .env in the CLI."""
    from graph import embed
    monkeypatch.setenv("NEBIUS_EMBED_MODEL", "")
    embed.use(embed.HashedEmbedder())
    yield
    embed.use(embed.HashedEmbedder())


@pytest.fixture(autouse=True)
def no_live_translation(monkeypatch):
    """Tests never call the translation model; a test that wants a translator passes its own."""
    monkeypatch.setattr("refresher.translate.client_from_environment", lambda env=None: None)
