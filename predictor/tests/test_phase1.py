"""Tests phase 1 : bugs simples."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_match(home_id: str, away_id: str, home_score: int, away_score: int, days_ago: float = 5.0) -> dict:
    return {
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_score": home_score,
        "away_score": away_score,
        "match_date": datetime.now(timezone.utc) - timedelta(days=days_ago),
    }


# ── 1.1 win_rate ─────────────────────────────────────────────────────────────

from predictor.src.features.rolling_stats import compute_rolling_stats


def test_win_rate_all_home_wins():
    """Équipe A gagne 5/5 à domicile → win_rate = 1.0."""
    matches = [_make_match("A", "B", 3, 0, 10 - i) for i in range(5)]
    stats = compute_rolling_stats("A", datetime.now(timezone.utc), matches, window=10)
    assert stats["win_rate"] == pytest.approx(1.0)


def test_win_rate_all_away_wins():
    """Équipe A gagne 5/5 à l'extérieur → win_rate = 1.0."""
    matches = [_make_match("B", "A", 0, 3, 10 - i) for i in range(5)]
    stats = compute_rolling_stats("A", datetime.now(timezone.utc), matches, window=10)
    assert stats["win_rate"] == pytest.approx(1.0)


def test_win_rate_zero():
    """Équipe A perd 5/5 → win_rate = 0.0."""
    matches = [_make_match("A", "B", 0, 3, 10 - i) for i in range(5)]
    stats = compute_rolling_stats("A", datetime.now(timezone.utc), matches, window=10)
    assert stats["win_rate"] == pytest.approx(0.0)


def test_win_rate_mixed():
    """3 wins, 2 losses → win_rate = 0.6."""
    wins   = [_make_match("A", "B", 2, 0, 10 - i) for i in range(3)]
    losses = [_make_match("A", "B", 0, 1, 7 - i) for i in range(2)]
    stats = compute_rolling_stats("A", datetime.now(timezone.utc), wins + losses, window=10)
    assert stats["win_rate"] == pytest.approx(0.6)


def test_win_rate_away_loss():
    """Équipe A perd 5/5 à l'extérieur → win_rate = 0.0."""
    matches = [_make_match("B", "A", 3, 0, 10 - i) for i in range(5)]
    stats = compute_rolling_stats("A", datetime.now(timezone.utc), matches, window=10)
    assert stats["win_rate"] == pytest.approx(0.0)
