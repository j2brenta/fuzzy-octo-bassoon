import json
import os
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from aiogram.types import Message

from channels.telegram import handle_message
from engine.llm import ModelError, ModelReply
from graph import relate
from refresher import consolidate as job, vault
from refresher.cli import main
from tests.test_proposals import AT, topical_vault
from tests.test_telegram import message


class FakeModel:
    """Answers the relate, namer and confirm prompts; counts calls by kind."""
    model_name = "fake"

    def __init__(self, same=True, fail=False):
        self.same, self.fail, self.calls = same, fail, {"relate": 0, "name": 0, "confirm": 0}

    def complete_json(self, system_prompt, content):
        if self.fail:
            raise ModelError("Nebius rejected the API key or model access.")
        if system_prompt == relate.SYSTEM_PROMPT:
            self.calls["relate"] += 1
            claims = json.loads(content)["claims"]
            return ModelReply({"verdicts": [{"claim": c["id"], "label": "NONE"} for c in claims]}, "fake")
        if system_prompt == job.NAMER_PROMPT:
            self.calls["name"] += 1
            return ModelReply({"title": f"Named topic {self.calls['name']}"}, "fake")
        self.calls["confirm"] += 1
        return ModelReply({"same": self.same}, "fake")


def test_lexical_consolidation_writes_proposals_views_and_the_stamp(tmp_path):
    v = topical_vault(tmp_path)
    assert job.due(v, AT)
    run = job.consolidate(v, at=AT)
    assert run.relate is None and run.proposed == 4 and run.waiting == 4
    assert list((v / "graph-reviews" / "proposals").glob("*.json"))
    assert (v / "wiki" / "graph" / "index.html").exists() and (v / "wiki" / "index.md").exists()
    assert not job.due(v, AT + timedelta(minutes=59)) and job.due(v, AT + timedelta(hours=1))
    assert "4 waiting for you" in (v / "log.md").read_text()
    assert "Review: 4 proposals waiting for you, 0 stale" in (v / "wiki" / "health.md").read_text()
    assert job.consolidate(v, at=AT).proposed == 0


def test_with_a_model_it_relates_names_topics_and_confirms_merges(tmp_path):
    v = topical_vault(tmp_path)
    model = FakeModel(same=False)
    run = job.consolidate(v, model, at=AT)
    assert run.relate.judged == 10 and model.calls["relate"] == 10
    kinds = {p.kind: p for p in vault.load_proposals(v)}
    assert "merge_ideas" not in kinds  # the model said the pair is not the same assertion
    titles = sorted(p.payload["title"] for p in vault.load_proposals(v) if p.kind == "topic")
    assert titles == ["Named topic 1", "Named topic 2"]


def test_a_failing_model_falls_back_to_wording_and_says_so(tmp_path):
    v = topical_vault(tmp_path)
    run = job.consolidate(v, FakeModel(fail=True), at=AT)
    assert "relations by wording only" in run.summary()
    assert run.proposed == 4  # lexical names and similarity-only merges


def test_cli_consolidate_lexical(tmp_path, capsys):
    v = topical_vault(tmp_path)
    assert main(["consolidate", "--vault", str(v), "--lexical", "--at", AT.isoformat()]) == 0
    assert "4 new proposals" in capsys.readouterr().out
    assert main(["proposals", "--vault", str(v)]) == 0
    assert "proposals: 4 waiting" in capsys.readouterr().out


class BotConsolidateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("channels.telegram.client_from_environment", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.vault = topical_vault(Path(self.directory.name) / "vault")

    async def test_consolidate_command_replies_with_what_waits(self):
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await handle_message(message(text="/consolidate"), self.vault, 100)
        replies = [c.args[0] for c in answer.call_args_list]
        assert replies[0].startswith("Reviewing the whole graph")
        assert "4 new proposals" in replies[-1] and "4 proposal(s) wait for you" in replies[-1]
