import json
import os
import unittest
from engine import language
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import pytest
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
        if system_prompt == language.apply(job.NAMER_PROMPT):
            self.calls["name"] += 1
            return ModelReply({"title": f"Named topic {self.calls['name']}"}, "fake")
        self.calls["confirm"] += 1
        return ModelReply({"same": self.same}, "fake")


def test_lexical_consolidation_writes_proposals_views_and_the_stamp(tmp_path):
    v = topical_vault(tmp_path)
    assert job.due(v, AT)
    run = job.consolidate(v, at=AT)
    assert run.relate.judged == 10 and run.proposed == 4 and run.waiting == 4  # wording verdicts stored
    assert list((v / "graph-reviews" / "proposals").glob("*.json"))
    assert (v / "wiki" / "graph" / "index.html").exists() and (v / "wiki" / "index.md").exists()
    assert not job.due(v, AT + timedelta(minutes=59)) and job.due(v, AT + timedelta(hours=1))
    assert "4 waiting for you" in (v / "log.md").read_text()
    assert "Review: 4 proposals waiting for you, 0 stale" in (v / "wiki" / "health.md").read_text()
    again = job.consolidate(v, at=AT)
    assert again.proposed == 0 and again.relate.judged == 0


def test_the_last_run_is_kept_for_the_app(tmp_path):
    v = topical_vault(tmp_path)
    assert job.last_run(v) is None
    (v / job.STAMP).parent.mkdir(exist_ok=True)
    (v / job.STAMP).write_text("2026-10-27T09:00:00+00:00")  # a run from before results were kept
    assert job.last_run(v) == {"at": "2026-10-27T09:00:00+00:00"}
    run = job.consolidate(v, at=AT)
    last = job.last_run(v)
    assert last["at"] == AT.isoformat() and last["proposed"] == 4 and last["related"] == 10
    assert last["summary"] == run.summary() and last["took"] >= 0 and last["model_calls"] == 0


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


# --- several runners: the bot, the review container, a scheduled job ---
def _place_something_new(v):
    post = next((v / "processed").glob("*.json"))
    later = post.stat().st_mtime + 3600
    os.utime(post, (later, later))  # placed after the last run's stamp


def test_if_due_runs_only_when_due_and_something_new_was_placed(tmp_path):
    v = topical_vault(tmp_path)
    assert job.consolidate(v, at=AT, if_due=True) is not None
    assert job.consolidate(v, at=AT + timedelta(minutes=30), if_due=True) is None  # not due
    assert job.consolidate(v, at=AT + timedelta(hours=2), if_due=True) is None  # due, but nothing new
    _place_something_new(v)
    assert job.consolidate(v, at=AT + timedelta(minutes=30), if_due=True) is None  # new, but not due
    assert job.consolidate(v, at=AT + timedelta(hours=2), if_due=True) is not None


def test_a_runner_that_waited_on_the_lock_does_not_review_again(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    v = topical_vault(tmp_path)
    with ThreadPoolExecutor(2) as pool:
        runs = list(pool.map(lambda _: job.consolidate(v, at=AT, if_due=True), range(2)))
    assert sum(r is not None for r in runs) == 1
    assert (v / "log.md").read_text().count("consolidate:") == 1


def test_cli_consolidate_if_due_says_when_it_skips(tmp_path, capsys):
    v = topical_vault(tmp_path)
    args = ["consolidate", "--vault", str(v), "--lexical", "--if-due", "--at", AT.isoformat()]
    assert main(args) == 0 and "4 new proposals" in capsys.readouterr().out
    assert main(args) == 0 and "consolidate: skipped" in capsys.readouterr().out


def test_cli_consolidate_every_keeps_checking_and_survives_a_failed_run(tmp_path, capsys):
    class Stop(Exception):
        pass
    v = topical_vault(tmp_path)
    runs = [OSError("disk full"), None, Stop()]
    real = job.consolidate

    def flaky(*args, **kwargs):
        outcome = runs.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return real(*args, **kwargs)
    with patch("refresher.consolidate.consolidate", side_effect=flaky), patch("time.sleep"), \
            pytest.raises(Stop):
        main(["consolidate", "--vault", str(v), "--lexical", "--every", "600"])
    assert "4 new proposals" in capsys.readouterr().out and not runs


def test_the_review_is_on_unless_turned_off():
    assert job.review_on({}) and job.review_on({"REFRESHER_REVIEW": "on"})
    assert not job.review_on({"REFRESHER_REVIEW": "OFF"})


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


# --- the spending cap per run ---

def test_the_budget_reserves_the_worst_case_and_charges_what_was_used():
    from engine.llm import BudgetedModel, BudgetExceeded

    class Tiny(FakeModel):
        max_tokens, timeout_seconds = 100, 10
    model = Tiny()
    budget = BudgetedModel(model, max_tokens=250, max_seconds=60)
    budget.complete_json(language.apply(job.NAMER_PROMPT), "- a")  # reserves ~78 prompt + 100 output tokens: fits in 250
    assert budget.calls == 1 and 78 < budget.spent < 100  # charged the prompt and the short answer, not the reserve
    with pytest.raises(BudgetExceeded, match="Run budget reached"):
        budget.complete_json(language.apply(job.NAMER_PROMPT), "- a")  # spent + the same reservation would pass 250
    assert model.calls["name"] == 1  # the refused call was never sent


def test_the_time_budget_refuses_a_call_that_could_run_past_it():
    from engine.llm import BudgetedModel, BudgetExceeded
    now = [0.0]
    budget = BudgetedModel(FakeModel(), max_tokens=10**9, max_seconds=300, clock=lambda: now[0])
    now[0] = 200.0  # 200 s gone, and a call may take its 120 s timeout
    with pytest.raises(BudgetExceeded, match="time budget"):
        budget.complete_json(language.apply(job.NAMER_PROMPT), "- a")


def test_a_run_over_budget_falls_back_to_wording_and_says_so(tmp_path):
    from engine.llm import BudgetedModel
    v = topical_vault(tmp_path)
    class Capped(FakeModel):
        max_tokens = 500
    model = Capped()
    run = job.consolidate(v, BudgetedModel(model, max_tokens=5_000, max_seconds=600), at=AT)
    assert "Run budget reached" in run.summary() and "model calls" in run.summary()
    assert 0 < model.calls["relate"] < 10  # stopped part-way
    assert all(vault.load_relation(v, ex) is not None for ex, _ in vault.load_extractions(v))  # the rest by wording
    assert run.proposed == 4
