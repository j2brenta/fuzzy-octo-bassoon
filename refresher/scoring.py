"""Knowledge half-life. Deterministic and pure: models supply the inputs, this decides the scores."""
from datetime import datetime

HALF_LIFE_DAYS = {"foundation": 720, "reference": 180, "news": 30, "ephemeral": 7}
CONFIRM_FACTOR = 1.5
MAX_HALF_LIFE = HALF_LIFE_DAYS["foundation"]
FRESH, FADING = 0.5, 0.25  # band thresholds: fresh >= 0.5 > fading >= 0.25 > stale
TRUST_PRIOR = 0.5


def elapsed_days(since: datetime, at: datetime) -> float:
    return max(0.0, (at - since).total_seconds() / 86400)


def freshness(half_life_days: float, clock_start: datetime, at: datetime, *, superseded: bool = False) -> float:
    """2^(-elapsed / half-life). A superseded claim keeps its chain but ranks at zero."""
    if superseded:
        return 0.0
    return 2 ** (-elapsed_days(clock_start, at) / half_life_days)


def confirm(half_life_days: float) -> float:
    """An independent confirmation extends the half-life (the caller also resets the clock)."""
    return min(half_life_days * CONFIRM_FACTOR, MAX_HALF_LIFE)


def half_life(base_days: float, confirmations: int) -> float:
    """The half-life after this many independent confirmations."""
    for _ in range(confirmations):
        base_days = confirm(base_days)
    return base_days


def band(score: float) -> str:
    return "fresh" if score >= FRESH else "fading" if score >= FADING else "stale"


def is_noise(durability: str, half_life_days: float, clock_start: datetime, at: datetime, *,
             confirmations: int = 0, duplicate_rank: int = 0) -> bool:
    """A repost beyond the first source, or an ephemeral claim unconfirmed after two half-lives."""
    if duplicate_rank > 0:
        return True
    return (durability == "ephemeral" and confirmations == 0
            and elapsed_days(clock_start, at) > 2 * half_life_days)


def trust_learned(confirmations: int = 0, contradictions: int = 0, up: int = 0, down: int = 0,
                  prior: float = TRUST_PRIOR, weight: float = 2.0) -> float:
    """Starts at the prior and moves with evidence: confirmations and thumbs up against
    supersessions or contradictions inside the half-life and thumbs down."""
    good, bad = confirmations + up, contradictions + down
    return (prior * weight + good) / (weight + good + bad)


def trust_rationale(confirmations: int = 0, contradictions: int = 0, up: int = 0, down: int = 0,
                    pinned: float | None = None, disagreements: int = 0) -> str:
    """Why a person's trust is what it is, in the words a people note shows."""
    if pinned is not None:
        return f"{pinned:.2f}: set by you"
    parts = [f"{confirmations} independent confirmation{'s' * (confirmations != 1)}",
             f"{contradictions} contradiction{'s' * (contradictions != 1)}"]
    if disagreements:
        parts.append(f"{disagreements} unresolved disagreements (not a penalty)")
    if up or down:
        parts.append(f"{up} up, {down} down from you")
    return f"{trust_learned(confirmations, contradictions, up, down):.2f}: " + ", ".join(parts)


def people_importance(subject_relevance: float, track_record: float, professional_relevance: float | None) -> float:
    """v1 collection ranking; missing professional evidence is neutral, never a negative judgment."""
    profile = 0.5 if professional_relevance is None else professional_relevance
    return subject_relevance * (0.6 * track_record + 0.4 * profile)
