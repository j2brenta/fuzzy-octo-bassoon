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


@pytest.fixture(autouse=True)
def no_problem_log_left_behind(monkeypatch):
    """Tests never write a problem log (the bot's default vault is the real one); test_problem_log turns it on."""
    monkeypatch.setattr("engine.logs.ENABLED", False)
    yield
    import logging
    from engine.logs import ProblemLog
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, ProblemLog)]:
        root.removeHandler(handler)
        handler.close()
