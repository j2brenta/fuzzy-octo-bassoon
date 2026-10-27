"""The hourly review job: relate what changed, review the whole graph, enrich people, rebuild the views.

    uv run refresher consolidate [--vault V] [--lexical]   or /consolidate in the bot; the bot also runs it after a
                                                          placement when the last run is more than an hour old

Everything runs under the vault lock, so a placement or an accept from the page waits for it rather than
interleaving. What the review proposes waits for the user (refresher serve); what it decides alone is applied.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from engine.llm import BudgetedModel, JsonModel, ModelError
from engine import language
from graph import cluster, relate
from refresher import graphview, vault
from refresher.place import RelateRun, _quiet, relate_pending

EVERY = timedelta(hours=1)
STAMP = Path(".cache") / "last-consolidate"
ENRICH_LIMIT = 3  # people looked up per run: Tavily is slow and paid

NAMER_PROMPT = """You name a topic in a personal knowledge base. You get statements that belong together.
Give the subject they share in 2 to 5 words, as a note title, in <<OUTPUT_LANGUAGE>>. No quotes, no period.
Return JSON: {"title": "..."}"""
CONFIRM_PROMPT = """Do these two statements make the same assertion, possibly worded differently? A statement that
adds a condition, a number or a different subject is not the same. Return JSON: {"same": true} or {"same": false}"""


def model_namer(model: JsonModel) -> Callable[[list[str]], str]:
    def name(statements: list[str]) -> str:
        try:
            title = str(model.complete_json(language.apply(NAMER_PROMPT), "\n".join(f"- {s}" for s in statements[:12])).data
                        .get("title") or "").strip()
        except ModelError:
            title = ""
        return title[:80] or cluster.title(statements)
    return name


def model_confirm(model: JsonModel) -> Callable[[str, str], bool]:
    def same(a: str, b: str) -> bool:
        try:
            return model.complete_json(CONFIRM_PROMPT, f"A: {a}\nB: {b}").data.get("same") is True
        except ModelError:
            return True  # similarity alone proposed it; the user still decides
    return same


@dataclass
class ConsolidateRun:
    at: datetime
    relate: RelateRun | None = None
    duplicates: int = 0
    firmed: int = 0
    proposed: int = 0
    waiting: int = 0
    enriched: int = 0
    notes: list[str] = None
    budget: BudgetedModel | None = None

    def summary(self) -> str:
        parts = []
        if self.relate is not None:
            parts.append(f"{self.relate.judged} items related")
        parts += [f"{self.duplicates} item duplicates", f"{self.firmed} Ideas firmed",
                  f"{self.proposed} new proposals", f"{self.waiting} waiting for you"]
        if self.enriched:
            parts.append(f"{self.enriched} people looked up")
        if self.budget is not None:
            parts.append(f"{self.budget.calls} model calls, about {self.budget.spent:,} tokens")
        return "consolidate: " + ", ".join(parts) + "".join(f"; {n}" for n in self.notes or [])


def due(vault_path: Path, at: datetime | None = None) -> bool:
    stamp = vault_path / STAMP
    if not stamp.exists():
        return True
    last = datetime.fromisoformat(stamp.read_text(encoding="utf-8").strip())
    return (at or datetime.now(timezone.utc)) - last >= EVERY


def consolidate(vault_path: Path, model: JsonModel | None = None, search=None, at: datetime | None = None,
                enrich_limit: int = ENRICH_LIMIT, log: Callable[[str], None] = _quiet,
                warn: Callable[[str], None] = _quiet) -> ConsolidateRun:
    """Without a model the job is lexical: items are related by wording (stored, so rebuilds stay fast; a later run
    with the model judges them again), topics are named by their shared words, and merges are proposed on
    similarity alone."""
    at = at or datetime.now(timezone.utc)
    run = ConsolidateRun(at, notes=[])
    if model is not None and not isinstance(model, BudgetedModel):
        model = BudgetedModel(model)  # one spending cap for the whole run: relating, naming, confirming, people
    run.budget = model
    with vault.lock(vault_path):
        if model is not None:
            run.relate = relate_pending(vault_path, relate.ModelRelator(model), log=log, warn=warn)
            if run.relate.error:
                run.notes.append(f"relations by wording only: {run.relate.error}")
                model = None  # the same key or network would fail the namer too
        if model is None:  # store wording verdicts for what the model did not judge, so rebuilds stay fast
            lexical = relate_pending(vault_path, relate.LexicalRelator(), log=log, warn=warn)
            run.relate = run.relate or lexical
        store = vault.replay(vault_path, include_unreviewed=True)
        report = store.review(at, namer=model_namer(model) if model else None,
                              confirm=model_confirm(model) if model else None)
        run.duplicates, run.firmed, run.proposed = len(report.item_duplicates), len(report.firmed), len(report.proposals)
        if report.item_duplicates or report.firmed or report.proposals:
            vault.save_review(vault_path, report)
        if search is not None and model is not None:
            store = vault.replay(vault_path, include_unreviewed=True)
            run.enriched, error = enrich_people(vault_path, store, pick_people(vault_path, store, enrich_limit),
                                                search, model, log, warn)
            if error:
                run.notes.append(f"enrichment stopped: {error}")
        store = vault.replay(vault_path, include_unreviewed=True)
        vault.export(store, vault_path, at)
        graphview.write(store, vault_path, at)
        run.waiting = sum(s.status == "proposed" for s in store.proposals.values())
        vault.atomic_write(vault_path / STAMP, at.isoformat())
        vault.log(vault_path, at, run.summary())
    return run


def pick_people(vault_path: Path, store, limit: int) -> list:
    """The most involved people not looked up yet."""
    def involvement(p):
        return 2 * len(p.facts) + 2 * len(p.authored) + len(p.mentioned_in)
    return sorted((p for p in store.people() if not vault.enriched(vault_path, p.person_id)),
                  key=lambda p: (-involvement(p), p.person_id))[:limit]


def enrich_people(vault_path: Path, store, chosen: list, search, model: JsonModel,
                  log: Callable[[str], None] = _quiet, warn: Callable[[str], None] = _quiet) -> tuple[int, str | None]:
    """Look each person up (engine/people.py) and store the record in people/. Returns (done, stopping error):
    a bad answer skips that person; a key, credit or network error stops the run."""
    from engine.llm import ModelOutputError
    from engine.people import SearchError, enrich
    claims = {c.claim.claim_id: c for c in store.claims()}
    orgs = store.organizations()
    done = 0
    for p in chosen:
        facts = [claims[cid].claim.statement for cid in sorted(p.facts) if cid in claims]
        titles = [store.item(i).title for i in sorted(p.authored | p.mentioned_in)]
        affiliation = next((name for name in orgs.values() for f in facts if name.casefold() in f.casefold()), None)
        context = facts + [f"Wrote: {store.item(i).title}" for i in sorted(p.authored)] + \
                  [f"Mentioned in: {store.item(i).title}" for i in sorted(p.mentioned_in)]
        try:
            record = enrich(p.person_id, p.name, context, affiliation, titles[0] if titles else None, search, model,
                            log=lambda message: log(f"{p.name}: {message}"))
        except ModelOutputError as error:
            warn(f"{p.name}: not enriched: {error}")
            continue
        except (SearchError, ModelError) as error:
            warn(f"{p.name}: not enriched: {error}")
            return done, str(error)
        vault.save_enrichment(vault_path, record)
        done += 1
        found = ", ".join(f["value"] for f in (record["role"], record["affiliation"]) if f) or "no match"
        log(f"{p.name}: {found}; {len(record['domains'])} domains")
    return done, None
