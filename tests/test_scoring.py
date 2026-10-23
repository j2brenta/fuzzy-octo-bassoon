from datetime import datetime, timedelta, timezone

import pytest

from refresher import scoring

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("durability,days", list(scoring.HALF_LIFE_DAYS.items()))
def test_one_half_life_halves_freshness(durability, days):
    assert scoring.freshness(days, T0, T0 + timedelta(days=days)) == pytest.approx(0.5)
    assert scoring.freshness(days, T0, T0) == 1.0


def test_superseded_ranks_zero_and_future_clock_is_fresh():
    assert scoring.freshness(180, T0, T0 + timedelta(days=1), superseded=True) == 0.0
    assert scoring.freshness(30, T0, T0 - timedelta(days=5)) == 1.0


def test_confirmation_grows_half_life_up_to_foundation():
    assert scoring.confirm(30) == 45
    assert scoring.confirm(600) == 720
    assert scoring.confirm(720) == 720


def test_bands():
    assert [scoring.band(x) for x in (1.0, 0.5, 0.49, 0.25, 0.1)] == ["fresh", "fresh", "fading", "fading", "stale"]


def test_noise_rules():
    assert scoring.is_noise("reference", 180, T0, T0, duplicate_rank=1)
    assert not scoring.is_noise("ephemeral", 7, T0, T0 + timedelta(days=14))
    assert scoring.is_noise("ephemeral", 7, T0, T0 + timedelta(days=15))
    assert not scoring.is_noise("ephemeral", 7, T0, T0 + timedelta(days=15), confirmations=1)
    assert not scoring.is_noise("news", 30, T0, T0 + timedelta(days=90))


def test_trust_starts_at_prior_and_moves_with_evidence():
    assert scoring.trust_learned() == 0.5
    assert scoring.trust_learned(confirmations=2) > 0.5 > scoring.trust_learned(contradictions=2)
    assert 0 < scoring.trust_learned(down=10) < scoring.trust_learned(down=1)
