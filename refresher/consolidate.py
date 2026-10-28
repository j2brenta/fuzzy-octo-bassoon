"""The hourly review job: relate what changed, review the whole graph, enrich people, rebuild the views.

    uv run refresher consolidate [--vault V] [--lexical]   or /consolidate in the bot; the bot also runs it after a
                                                          placement when the last run is more than an hour old
    uv run refresher consolidate --if-due [--every 600]   only when due and something new was placed: the review
                                                          container loops on it, a scheduled serverless job runs it once

The bot, the review container and a scheduled job may all run it: with --if-due the check is repeated under the lock,
so whoever comes second sees the first one's stamp and skips. REFRESHER_REVIEW=off turns the bot's own runs off.

Everything runs under the vault lock, so a placement or an accept from the page waits for it rather than
interleaving. What the review proposes waits for the user (refresher serve); what it decides alone is applied.
"""
from dataclasses import dataclass
import json
import os
import time
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from engine.llm import BudgetedModel, JsonModel, ModelError
from engine import language
from graph import cluster, relate
from refresher import graphview, summarize, vault
from refresher.place import RelateRun, _quiet, _warn, relate_pending

EVERY = timedelta(hours=1)
STAMP = Path(".cache") / "last-consolidate"
LAST_RUN = Path(".cache") / "last-consolidate.json"  # what the last run did, for the app's review panel
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


THEME_PROMPT = """You sort statements from a personal knowledge base into themes. You get existing groups (id,
name, one example statement) and statements that belong to none yet. Put each statement into the existing group it
fits, or give it a new theme: 2 to 5 words in <<OUTPUT_LANGUAGE>> naming a broad subject, not a single event or detail.
A new theme must hold at least two statements, and use at most <<MAX_THEMES>> new themes: a statement that shares a
subject with no other goes into the closest existing group. Prefer an existing group whenever it roughly fits.
Return JSON: {"placements": [{"statement": "s1", "group": "g2"}, {"statement": "s2", "theme": "Personal savings"}]}
with every statement exactly once and either a group or a theme."""
THEME_BATCH = 60  # statements per call


def assign_themes(vault_path: Path, store, model: JsonModel | None, log: Callable[[str], None] = _quiet) -> int:
    """Give every Idea no cluster holds a home: a cluster, or a theme of its own, by one short call per batch.
    Placements are stored by canonical claim, so only new Ideas are placed later; a failed call places nothing."""
    if model is None:
        return 0
    held = {i for c in store.clusters() for i in c.ideas}
    loose = sorted((i for i in store.ideas() if i.idea_id not in held), key=lambda i: i.idea_id)
    if not loose:
        return 0
    groups = [(c.cluster_id, c.title, store.idea(min(c.ideas)).title) for c in sorted(store.clusters(), key=lambda c: c.cluster_id)]
    placed: dict[str, dict] = {}
    for start in range(0, len(loose), THEME_BATCH):
        batch = loose[start:start + THEME_BATCH]
        new_themes = sorted({p["theme"] for p in placed.values() if p.get("cluster") is None})
        offered = groups + [(None, t, "") for t in new_themes]
        content = json.dumps({"groups": [{"id": f"g{n}", "name": name, "example": example}
                                         for n, (_, name, example) in enumerate(offered, 1)],
                              "statements": [{"id": f"s{n}", "text": i.title} for n, i in enumerate(batch, 1)]},
                             ensure_ascii=False)
        prompt = language.apply(THEME_PROMPT).replace("<<MAX_THEMES>>", str(max(1, len(batch) // 4)))
        try:
            rows = model.complete_json(prompt, content).data.get("placements")
        except ModelError as error:
            log(f"themes: not placed ({type(error).__name__})")
            continue
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not str(row.get("statement", "")).startswith("s"):
                continue
            try:
                idea = batch[int(row["statement"][1:]) - 1]
            except (ValueError, IndexError):
                continue
            group = row.get("group")
            if isinstance(group, str) and group.startswith("g") and group[1:].isdigit() and 0 < int(group[1:]) <= len(offered):
                cluster_id, name, _ = offered[int(group[1:]) - 1]
            elif isinstance(row.get("theme"), str) and row["theme"].strip():
                cluster_id, name = None, row["theme"].strip()[:80]
            else:
                continue
            placed[idea.canonical_claim_id] = {"cluster": cluster_id, "theme": name, "model": model.model_name}
    # A new theme the model used for one statement only is no grouping: that Idea goes to the nearest cluster.
    clusters = {c.cluster_id: c for c in store.clusters()}
    by_claim = {i.canonical_claim_id: i for i in loose}
    sizes: dict[str, int] = {}
    for p in placed.values():
        if p["cluster"] is None:
            sizes[p["theme"]] = sizes.get(p["theme"], 0) + 1
    for claim_id, p in placed.items():
        if p["cluster"] is None and sizes[p["theme"]] == 1 and clusters:
            [vector] = store._idea_vectors([by_claim[claim_id].idea_id])
            [nearest] = cluster.attach_all(vector, {cid: c.centroid for cid, c in clusters.items()}, threshold=-1.0,
                                           limit=1)
            p.update(cluster=nearest, theme=clusters[nearest].title, nearest=True)
    if placed:
        vault.save_themes(vault_path, placed)
        store.use_themes(vault.load_themes(vault_path))
    return len(placed)


def name_clusters(vault_path: Path, store, model: JsonModel | None, log: Callable[[str], None] = _quiet) -> int:
    """Name every cluster whose membership has no stored name, one short call each; the store takes the names.
    A failed call stores nothing: the cluster keeps its central Idea as a title and is named next time."""
    if model is None:
        return 0
    known = vault.load_cluster_names(vault_path)
    fresh = {}
    for c in sorted(store.clusters(), key=lambda c: c.cluster_id):
        if not c.fingerprint or c.fingerprint in known:  # a theme came with its name
            continue
        statements = [store.idea(i).title for i in sorted(c.ideas)]
        try:
            title = str(model.complete_json(language.apply(NAMER_PROMPT), "\n".join(f"- {s}" for s in statements[:12]))
                        .data.get("title") or "").strip()[:80]
        except ModelError as error:
            log(f"cluster {c.cluster_id}: not named ({type(error).__name__})")
            continue
        if title:
            fresh[c.fingerprint] = {"title": title, "model": model.model_name, "statements": statements[:12]}
    if fresh:
        vault.save_cluster_names(vault_path, fresh)
        store.use_cluster_names({**known, **{k: v["title"] for k, v in fresh.items()}})
    return len(fresh)


def summarize_items(vault_path: Path, store, model: JsonModel | None, log: Callable[[str], None] = _quiet) -> int:
    """A hook and a gist for every visible item without a current summary (refresher/summarize.py), one call each.
    A failed call stores nothing: the reading map shows the item's lead sentences and it is summarised next time."""
    if model is None:
        return 0
    done = 0
    for item_id in summarize.missing(vault_path, sorted(i.item_id for i in store.items()), model):
        try:
            summarize.summarize_item(vault_path, item_id, model)
        except (ModelError, OSError, ValueError, KeyError) as error:
            log(f"item {item_id[:10]}: not summarised ({type(error).__name__})")
            continue
        done += 1
    return done


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
    named: int = 0  # clusters given a name this run
    placed: int = 0  # Ideas a model gave a cluster or theme this run
    summarized: int = 0  # items given a hook and a gist this run
    notes: list[str] = None
    budget: BudgetedModel | None = None
    took: float = 0.0  # seconds, waiting for the lock included

    def record(self) -> dict:
        return {"at": self.at.isoformat(), "took": round(self.took, 1), "summary": self.summary(),
                "related": self.relate.judged if self.relate else 0, "duplicates": self.duplicates,
                "firmed": self.firmed, "proposed": self.proposed, "waiting": self.waiting, "enriched": self.enriched,
                "named": self.named, "placed": self.placed, "summarized": self.summarized,
                "notes": list(self.notes or []),
                "model_calls": self.budget.calls if self.budget else 0,
                "tokens": self.budget.spent if self.budget else 0}

    def summary(self) -> str:
        parts = []
        if self.relate is not None:
            parts.append(f"{self.relate.judged} items related")
        parts += [f"{self.duplicates} item duplicates", f"{self.firmed} Ideas firmed",
                  f"{self.proposed} new proposals", f"{self.waiting} waiting for you"]
        if self.enriched:
            parts.append(f"{self.enriched} people looked up")
        if self.named:
            parts.append(f"{self.named} clusters named")
        if self.placed:
            parts.append(f"{self.placed} Ideas placed in themes")
        if self.summarized:
            parts.append(f"{self.summarized} items summarised")
        if self.budget is not None:
            parts.append(f"{self.budget.calls} model calls, about {self.budget.spent:,} tokens")
        return "consolidate: " + ", ".join(parts) + "".join(f"; {n}" for n in self.notes or [])


def due(vault_path: Path, at: datetime | None = None) -> bool:
    stamp = vault_path / STAMP
    if not stamp.exists():
        return True
    last = datetime.fromisoformat(stamp.read_text(encoding="utf-8").strip())
    return (at or datetime.now(timezone.utc)) - last >= EVERY


def pending(vault_path: Path, at: datetime | None = None) -> bool:
    """The hourly review is due and a post was placed since it last ran: captures in a burst still get reviewed
    without waiting for the next one."""
    processed = vault_path / "processed"
    if not processed.is_dir() or not due(vault_path, at):
        return False
    stamp = vault_path / STAMP
    last = stamp.stat().st_mtime if stamp.exists() else 0.0
    return any(p.stat().st_mtime > last for p in processed.glob("*.json"))


def last_run(vault_path: Path) -> dict | None:
    """What the last run did (ConsolidateRun.record); only its time when it ran before runs were recorded; None
    before the first one."""
    try:
        return json.loads((vault_path / LAST_RUN).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        return {"at": datetime.fromisoformat((vault_path / STAMP).read_text(encoding="utf-8").strip()).isoformat()}
    except (OSError, ValueError):
        return None


def models_from_environment(lexical: bool = False):
    """(model, search) for a run: the model unless `lexical` or GRAPH_RELATOR=lexical, and Tavily with the model
    when its key is set. Raises ModelError when the model is configured badly."""
    if lexical or os.environ.get("GRAPH_RELATOR", "model").strip().lower() == "lexical":
        return None, None
    from engine.llm import client_from_environment
    from engine.people import search_from_environment
    return client_from_environment(), search_from_environment()


def review_on(env=None) -> bool:
    """The hourly review runs by default; REFRESHER_REVIEW=off is the opt-out for a runner another one covers."""
    return (os.environ if env is None else env).get("REFRESHER_REVIEW", "on").strip().lower() != "off"


def consolidate(vault_path: Path, model: JsonModel | None = None, search=None, at: datetime | None = None,
                enrich_limit: int = ENRICH_LIMIT, log: Callable[[str], None] = _quiet,
                warn: Callable[[str], None] = _warn, if_due: bool = False) -> ConsolidateRun | None:
    """Without a model the job is lexical: items are related by wording (stored, so rebuilds stay fast; a later run
    with the model judges them again), topics are named by their shared words, and merges are proposed on
    similarity alone. With `if_due`, None when the review is not pending once the lock is held: another runner did
    it while this one waited."""
    at = at or datetime.now(timezone.utc)
    started = time.monotonic()
    run = ConsolidateRun(at, notes=[])
    if model is not None and not isinstance(model, BudgetedModel):
        model = BudgetedModel(model)  # one spending cap for the whole run: relating, naming, confirming, people
    run.budget = model
    with vault.lock(vault_path):
        if if_due and not pending(vault_path, at):
            return None
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
        from refresher import translate
        namer = translate.client_from_environment()  # the cheap model only; without it clusters keep their central Idea
        run.named = name_clusters(vault_path, store, namer, log) if namer is not None else 0
        run.placed = assign_themes(vault_path, store, namer, log) if namer is not None else 0
        run.summarized = summarize_items(vault_path, store, summarize.client_from_environment(), log)
        if run.summarized:
            store.summaries = summarize.load_all(vault_path)
        vault.export(store, vault_path, at)
        graphview.write(store, vault_path, at)
        run.waiting = sum(s.status == "proposed" for s in store.proposals.values())
        run.took = time.monotonic() - started
        vault.atomic_write(vault_path / LAST_RUN, json.dumps(run.record(), ensure_ascii=False, indent=1))
        vault.atomic_write(vault_path / STAMP, at.isoformat())
        vault.log(vault_path, at, run.summary())
    return run


def pick_people(vault_path: Path, store, limit: int) -> list:
    """The most involved people not looked up yet."""
    def involvement(p):
        return 2 * len(p.facts) + 2 * len(p.authored) + 2 * len(p.statements) + len(p.mentioned_in)
    def eligible(p):
        if vault.enriched(vault_path, p.person_id):
            return False
        if p.authored or not p.mentioned_in:
            return True
        # Protect old accepted artifacts too: do not pay to look up an eponym.
        from engine.entities import _eponym_only
        for item_id in p.mentioned_in:
            text = store.item(item_id).text
            mentions = [{"start_char": m.start(), "end_char": m.end()}
                        for m in re.finditer(r"(?<!\w)" + re.escape(p.name) + r"(?!\w)", text, re.I)]
            if not _eponym_only({"mentions": mentions}, text):
                return True  # Missing context is not evidence to delete an identity.
        return False
    return sorted((p for p in store.people() if eligible(p)),
                  key=lambda p: (-involvement(p), p.person_id))[:limit]


def enrich_people(vault_path: Path, store, chosen: list, search, model: JsonModel,
                  log: Callable[[str], None] = _quiet, warn: Callable[[str], None] = _warn) -> tuple[int, str | None]:
    """Look each person up (engine/people.py) and store the record in people/. Returns (done, stopping error):
    a bad answer skips that person; a key, credit or network error stops the run."""
    from engine.llm import ModelOutputError
    from engine.people import SearchError, enrich
    claims = {c.claim.claim_id: c for c in store.claims()}
    orgs = {o.org_id: o.name for o in store.organizations()}
    ties = {}  # person -> the organizations items say they belong to
    for a in store.affiliations():
        if a.item_id:
            ties.setdefault(a.person_id, []).append(orgs[a.org_id])
    done = 0
    for p in chosen:
        facts = [claims[cid].claim.statement for cid in sorted(p.facts) if cid in claims]
        titles = [store.item(i).title for i in sorted(p.authored | p.mentioned_in)]
        affiliation = next(iter(ties.get(p.person_id, [])), None) or \
            next((name for name in orgs.values() for f in facts if name.casefold() in f.casefold()), None)
        context = ([f"Profile identity checked by the user: {p.identity_url}"] if p.identity_url else []) + facts + [f"Wrote: {store.item(i).title}" for i in sorted(p.authored)] + \
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
