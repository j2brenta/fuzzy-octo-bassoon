"""Proposals the review makes and the user decides, with the stale check from Victor's news map.

A proposal names the claims (or people) it covers and keeps a fingerprint of what they said. A decision keeps the
fingerprint the user saw. The token the accept screen hands out hashes the proposal, the current fingerprint and the
latest decision; a decision sent with an old token is refused, so nobody approves evidence they did not see. An
accepted proposal applies only while its fingerprint still matches; after a re-extraction it is stale, and the next
review proposes it again from the current graph.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from graph.store import Decision, Proposal

ACTIONS = ("accept", "reject", "defer")


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def proposal_id(kind: str, payload: dict) -> str:
    """Stable: the same suggestion made again reuses its file and its decisions."""
    return "p-" + _hash([kind, payload])[:16]


def fingerprint(store, kind: str, payload: dict) -> str | None:
    """What the covered claims (or people) say now; None when one of them is not in the graph."""
    if kind == "merge_people":
        people = {p.person_id: p for p in store.people()}
        if not all(pid in people for pid in payload["people"]):
            return None
        return _hash([[pid, people[pid].name] for pid in payload["people"]])
    claims = {c.claim.claim_id: c for c in store.claims(hidden=True)}
    if not all(cid in claims for cid in payload["claims"]):
        return None
    return _hash([[cid, claims[cid].item_id, claims[cid].claim.statement] for cid in payload["claims"]])


def make(store, kind: str, payload: dict, rationale: str, at: datetime) -> Proposal:
    return Proposal(proposal_id(kind, payload), kind, payload, rationale, at, fingerprint(store, kind, payload))


@dataclass
class ProposalState:
    proposal: Proposal
    status: str  # proposed | accepted | rejected | stale | unplaced
    token: str
    reason: str = ""
    decisions: list[Decision] = field(default_factory=list)

    @property
    def decided(self) -> Decision | None:
        return self.decisions[-1] if self.decisions else None


def state(store, proposal: Proposal, decisions: list[Decision]) -> ProposalState:
    """Where a proposal stands against the graph now (before any proposal is applied)."""
    current = fingerprint(store, proposal.kind, proposal.payload)
    mine = sorted((d for d in decisions if d.proposal_id == proposal.proposal_id), key=lambda d: d.at)
    latest = next((d for d in reversed(mine) if d.action != "defer"), None)
    token = _hash([proposal.proposal_id, proposal.fingerprint, current, latest.token if latest else None,
                   latest.action if latest else None])
    if current is None:
        return ProposalState(proposal, "unplaced", token, "A claim or person it covers is not in this graph.", mine)
    if latest is not None and latest.fingerprint == current:
        return ProposalState(proposal, "accepted" if latest.action == "accept" else "rejected", token, "", mine)
    if proposal.fingerprint != current:
        return ProposalState(proposal, "stale", token,
                             "What it covers changed since it was proposed; the next review proposes it again.", mine)
    return ProposalState(proposal, "proposed", token, "", mine)
