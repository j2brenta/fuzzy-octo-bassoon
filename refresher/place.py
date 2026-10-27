"""Place a captured item in the graph and say where it went: the bot's reply after ingestion.

The vault stays the truth. The item's relation verdicts are judged once and stored in relations/, then the whole
vault is replayed, so the reply shows the graph a rebuild would show, including items that arrived earlier but
happened later.
"""
from dataclasses import dataclass
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from pydantic import ValidationError

from engine.llm import ModelError, ModelOutputError
from graph import relate
from graph.memory import MemoryStore
from graph.store import Placement
from refresher import graphview, translate, vault

SHOWN = 3  # Ideas and relations listed in a reply before "and N more"

logger = logging.getLogger(__name__)


@dataclass
class RelateRun:
    judged: int = 0
    failed: int = 0
    error: str | None = None  # a key, credit, rate limit or network error stopped the run


def _warn(message: str) -> None:
    """Problems go to the log (and so to the vault's problem log), not only to whoever called."""
    logger.warning(message)


def _quiet(message: str) -> None:
    pass


def relate_pending(vault_path: Path, relator: relate.Relator, only: Iterable[str] | None = None,
                   log: Callable[[str], None] = _quiet, warn: Callable[[str], None] = _warn) -> RelateRun:
    """Replay in event-time order and judge each item that has no current verdict file against what precedes it.
    Every item is judged, unreviewed ones too, so the files serve rebuilds with or without --include-unreviewed.
    With `only`, just those items are judged; the others are related lexically on the way, as a rebuild would.
    Verdicts stored by wording count as not yet judged when a model relator runs.

    Stored verdicts are judged again, whatever `only` says, when a close candidate they never saw has appeared
    (relate.stale): an original that arrives after its correction. That check is costly, so it runs only when an
    item placed before this one arrived after it was judged (Relation.pool). Model verdicts are never redone by
    wording."""
    only = set(only) if only is not None else None
    run = RelateRun()
    store = MemoryStore(include_unreviewed=True)
    with store.deferred():  # the graph is recomputed only when a judgement needs it (candidate_pool)
        for ex, text in vault.load_extractions(vault_path):
            relation = _relate_one(vault_path, store, ex, text, relator, only, run, log, warn)
            if run.error:
                return run
            store.apply(ex, text, relation)
    return run


def _relate_one(vault_path, store, ex, text, relator, only, run, log, warn):
    relation = vault.load_relation(vault_path, ex)
    upgrade = relation is not None and relation.relator == "lexical" and relator.name != "lexical"
    missing = (relation is None or upgrade) and (only is None or ex.item_id in only)
    pool = store.placed_fingerprint()
    recheck = False
    if relation is not None and not missing and relation.pool != pool and ex.claims \
            and not (relation.relator == "model" and relator.name == "lexical"):
        recheck = relate.stale(relation, ex, store.candidate_pool(ex.item_id))
        if not recheck:  # nothing closer arrived: remember what was checked
            relation = relation.model_copy(update={"pool": pool})
            vault.save_relation(vault_path, relation)
    if (missing or recheck) and ex.claims:
        try:
            judged = relate.judge(ex, text, store.candidate_pool(ex.item_id), relator)
        except (ModelOutputError, ValidationError) as error:  # a bad answer: the item waits for the next run
            warn(f"{ex.item_id[:12]}: not judged: {error}")
            run.failed += 1
        except ModelError as error:  # key, credit, rate limit or network: every other item would fail too
            warn(f"{ex.item_id[:12]}: not judged: {error}")
            run.error = str(error)
        else:
            relation = judged.model_copy(update={"pool": pool})
            vault.save_relation(vault_path, relation)
            run.judged += 1
            related = sum(v.label != "NONE" for v in relation.verdicts)
            log(f"{ex.item_id[:12]}: {len(relation.verdicts)} claims, {related} related ({relator.name})"
                + (", judged again: a closer candidate arrived" if recheck else ""))
    return relation


def place(vault_path: Path, item_id: str, relator: relate.Relator | None = None,
          at: datetime | None = None) -> tuple[Placement, MemoryStore, str | None]:
    """Judge the item's relations with `relator`, store them, replay the vault, refresh wiki/ and return where the
    item went. Without a relator, or when the model fails, the item is related lexically and not stored, so a later
    `refresher relate` can still judge it; the note says so."""
    with vault.lock(vault_path):  # the hourly job and the accept page write the same files
        return _place(vault_path, item_id, relator, at)


def _place(vault_path, item_id, relator, at):
    note = None
    if relator is not None:
        run = relate_pending(vault_path, relator, only=[item_id])
        if run.error:
            note = f"Relations matched by wording only: the model is unavailable ({run.error})"
        elif run.failed:
            note = "Relations matched by wording only: the model's answer did not validate"
    # Without a model verdict the item is related by wording, and that is stored too, so rebuilds do not judge it
    # again; a later `refresher relate` or consolidate with the model replaces it.
    relate_pending(vault_path, relate.LexicalRelator(), only=[item_id])
    stored = vault.load_relation(vault_path, next(ex for ex, _ in vault.load_extractions(vault_path)
                                                  if ex.item_id == item_id))
    if note is None and (stored is None or stored.relator == "lexical"):
        note = "Relations matched by wording only"
    translator = translate.client_from_environment()
    if translator is not None:  # for reading on the map and in notes; a failure only leaves the original
        try:
            translate.translate_item(vault_path, item_id, translator)
        except (ModelError, OSError, ValueError, KeyError) as error:
            logger.warning("item %s · translation failed (%s); the original is shown alone", item_id[:10],
                           type(error).__name__)
    at = at or datetime.now(timezone.utc)
    store = vault.replay(vault_path, include_unreviewed=True)
    if translator is not None:
        from refresher.consolidate import assign_themes, name_clusters
        name_clusters(vault_path, store, translator, log=logger.info)
        assign_themes(vault_path, store, translator, log=logger.info)
    vault.export(store, vault_path, at)
    graphview.write(store, vault_path, at)
    placement = store.placement(item_id)
    vault.log(vault_path, at, f"place: {item_id[:12]} {placement.status}, {placement.claims} claims, "
                              f"{len(placement.ideas)} Ideas, {len(placement.relations)} relations")
    return placement, store, note


def _short(text: str, limit: int = 90) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _more(names: list[str]) -> str:
    shown = "; ".join(names[:SHOWN])
    return shown + (f"; and {len(names) - SHOWN} more" if len(names) > SHOWN else "")


def reply_text(placement: Placement, store: MemoryStore, note: str | None = None) -> str:
    """Filed under, by whom, duplicate of, supersedes: the lines the bot sends after placing an item."""
    p = placement
    claims = {c.claim.claim_id: c for c in store.claims(hidden=True)}
    mine = {cid for cid, c in claims.items() if c.item_id == p.item_id}

    def said(cid: str) -> str:
        return f"«{_short(claims[cid].claim.statement)}»"

    def name(node_id: str) -> str:
        if node_id.startswith("person:"):
            return store.person(node_id).name
        return store.organizations().get(node_id, node_id)

    lines = []
    if p.status != "placed":
        lines.append(f"Held back from the graph: {store.held_back.get(p.item_id, 'not placed')}.")
    if p.ideas:
        lines.append("Filed under: " + _more([f"{store.idea(i).title} ({store.idea(i).status})" for i in p.ideas]))
    subjects = list(dict.fromkeys(e.dst for e in store.edges() if e.type == "ABOUT" and e.src in mine))
    if subjects:
        lines.append("About: " + _more([name(s) for s in subjects]))
    if p.status == "placed":
        lines.append(f"By: {store.person(p.author).name}" if p.author else "By: author unknown")
    if p.duplicate_of:
        lines.append(f"Duplicate of: {store.item(p.duplicate_of).title}")

    relations, repeats = [], 0
    for e in p.relations:
        if e.src not in claims or e.dst not in claims:
            continue  # item-level DUPLICATES, covered by "Duplicate of"
        ours, other = (e.src, e.dst) if e.src in mine else (e.dst, e.src)
        if e.type == "SUPERSEDES":
            relations.append(f"Supersedes {said(e.dst)} with {said(e.src)}" if e.src in mine
                             else f"Superseded: {said(e.dst)} is now {said(e.src)}")
        elif e.type == "SUPPORTS":
            relations.append(f"Confirms {said(other)}" if e.src in mine else f"Confirmed later: {said(ours)}")
        elif e.type == "CONTRADICTS":
            relations.append(f"Contradicts {said(other)}")
        elif e.type == "ELABORATES":
            relations.append(f"Adds detail to {said(other)}" if e.src in mine else f"Detailed later: {said(ours)}")
        elif e.type == "DUPLICATES" and e.src in mine:
            repeats += 1
    relations = list(dict.fromkeys(relations))
    lines += relations[:SHOWN]
    if len(relations) > SHOWN:
        lines.append(f"and {len(relations) - SHOWN} more relations")
    if repeats and not p.duplicate_of:
        lines.append(f"{repeats} claim(s) repeat what the graph already holds")

    item = store.item(p.item_id) if p.status == "placed" else None
    if item is not None and item.review_status != "accepted":
        lines.append(f"Unreviewed: ingestion quality is {item.review_status}.")
    if note:
        lines.append(note + ".")
    return "\n".join(lines) if lines else "Placed; nothing related yet."
