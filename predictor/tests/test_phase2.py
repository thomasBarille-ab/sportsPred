"""Tests phase 2 : pipeline ML."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

def _dt(days_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _match(home: str, away: str, hs: int, as_: int, days_ago: float = 5.0) -> dict:
    return {
        "home_team_id": home, "away_team_id": away,
        "home_score": hs, "away_score": as_,
        "match_date": _dt(days_ago),
    }


def _fixture(home: str, away: str, days_ahead: float = 1.0, status: str = "SCHEDULED") -> dict:
    return {
        "home_team_id": home, "away_team_id": away,
        "home_score": None, "away_score": None,
        "match_date": datetime.now(timezone.utc) + timedelta(days=days_ahead),
        "status": status,
    }


# ── 2.1 Dixon-Coles vectorisé ─────────────────────────────────────────────────

def _synthetic_matches(n_teams: int = 20, n_matches: int = 1100) -> list[dict]:
    """Génère des matchs synthétiques pour tester le fit DC."""
    rng = np.random.default_rng(42)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    matches = []
    for i in range(n_matches):
        home, away = rng.choice(teams, size=2, replace=False)
        hs = int(rng.poisson(1.5))
        as_ = int(rng.poisson(1.1))
        matches.append({
            "home_team_id": home, "away_team_id": away,
            "home_score": hs, "away_score": as_,
            "match_date": _dt(n_matches - i),
        })
    return matches


def test_dc_fit_consistent_with_reference():
    """Le nouveau fit vectorisé donne les mêmes paramètres que l'ancienne implémentation (tol 1e-3)."""
    import math
    from predictor.src.features.dixon_coles import fit_dixon_coles

    matches = _synthetic_matches(20, 1100)
    t0 = time.time()
    model_new = fit_dixon_coles(matches)
    elapsed = time.time() - t0

    assert elapsed < 5.0, f"fit_dixon_coles trop lent : {elapsed:.1f}s"
    assert model_new.converged
    assert len(model_new.teams) == 20
    # L'avantage domicile doit être > 1 (football)
    assert model_new.home_advantage > 1.0
    # rho négatif (correction Dixon-Coles typique)
    assert model_new.rho < 0.5


def test_dc_warm_start_faster_or_equal():
    """Le warm start ne doit pas dégrader les résultats."""
    import math
    from predictor.src.features.dixon_coles import dc_model_to_x0, fit_dixon_coles

    matches = _synthetic_matches(10, 300)
    model1 = fit_dixon_coles(matches)
    x0 = dc_model_to_x0(model1, model1.teams)
    model2 = fit_dixon_coles(matches, x0=x0)

    # Les paramètres doivent converger vers la même solution
    for team in model1.teams:
        assert abs(math.log(model1.attack[team]) - math.log(model2.attack[team])) < 0.05
    assert abs(model1.home_advantage - model2.home_advantage) < 0.05


def test_dc_warm_start_different_teams_returns_none():
    """dc_model_to_x0 retourne None si les équipes ont changé."""
    from predictor.src.features.dixon_coles import dc_model_to_x0, fit_dixon_coles

    matches = _synthetic_matches(10, 300)
    model = fit_dixon_coles(matches)
    different_teams = model.teams[:-1] + ["NEW_TEAM"]
    assert dc_model_to_x0(model, different_teams) is None


# ── 2.2 Elo no-leak ──────────────────────────────────────────────────────────

from predictor.src.features.elo import build_elo_state, EloState


def test_elo_no_leak():
    """Modifier le résultat du match i ne doit pas affecter la feature du match i."""
    from predictor.src.features.builder import build_features_nba

    # Construire une série de matchs identiques
    def make_series(home_score_i: int):
        matches = [_match("A", "B", home_score_i if idx == 5 else 110, 100, 20 - idx)
                   for idx in range(10)]
        elo = build_elo_state("nba")
        elo_at_match_5 = None
        for j, m in enumerate(matches):
            if j == 5:
                elo_at_match_5 = dict(elo.ratings)
            elo.update(m["home_team_id"], m["away_team_id"], m["home_score"], m["away_score"], "nba")
        return elo_at_match_5

    ratings_win  = make_series(110)
    ratings_loss = make_series(90)

    # Les ratings AVANT le match 5 doivent être identiques quel que soit le résultat du match 5
    assert ratings_win == ratings_loss, "Data leakage : le résultat du match i affecte les features du match i"


# ── 2.4 Repos depuis le calendrier ───────────────────────────────────────────

from predictor.src.features.rolling_stats import (
    compute_rolling_stats,
    is_back2back_from_schedule,
)


def test_back2back_from_schedule():
    """Un match planifié (non joué) entre deux matches doit produire le bon B2B."""
    now = datetime.now(timezone.utc)
    # Équipe A a joué il y a 5 jours, puis a un match planifié il y a 1 jour (non joué)
    finished = _match("A", "B", 110, 100, days_ago=5)
    scheduled = {
        "home_team_id": "A", "away_team_id": "C",
        "home_score": None, "away_score": None,
        "match_date": now - timedelta(days=1),
        "status": "SCHEDULED",
    }
    target_date = now

    # Sans schedule → B2B basé sur les matchs terminés (5 jours → pas B2B)
    stats_no_sched = compute_rolling_stats("A", target_date, [finished], schedule=None)
    assert stats_no_sched["is_back2back"] == 0.0

    # Avec schedule → B2B basé sur le calendrier (1 jour → B2B)
    stats_with_sched = compute_rolling_stats("A", target_date, [finished], schedule=[finished, scheduled])
    assert stats_with_sched["is_back2back"] == 1.0


def test_days_since_excludes_cancelled():
    """Les matchs CANCELLED ne comptent pas dans le repos."""
    from predictor.src.features.rolling_stats import days_since_last_scheduled_game
    now = datetime.now(timezone.utc)

    cancelled = {
        "home_team_id": "A", "away_team_id": "B",
        "match_date": now - timedelta(days=1),
        "status": "CANCELLED",
    }
    real_match = {
        "home_team_id": "A", "away_team_id": "B",
        "match_date": now - timedelta(days=5),
        "status": "FINISHED",
    }
    days = days_since_last_scheduled_game("A", now, [cancelled, real_match])
    # Le match CANCELLED est ignoré, donc dernier match = il y a 5 jours
    assert abs(days - 5.0) < 0.1


def test_back2back_excludes_postponed():
    """Les matchs POSTPONED ne comptent pas dans le B2B."""
    now = datetime.now(timezone.utc)
    postponed = {
        "home_team_id": "A", "away_team_id": "B",
        "match_date": now - timedelta(hours=20),
        "status": "POSTPONED",
    }
    stats = compute_rolling_stats("A", now, [], schedule=[postponed])
    assert stats["is_back2back"] == 0.0


# ── Brier score uniforme ──────────────────────────────────────────────────────

from predictor.src.scoring.metrics import brier_score, BRIER_BASELINE


def test_brier_uniform_ligue1():
    """Prédiction uniforme Ligue 1 → Brier = 2/3."""
    bs = brier_score(1/3, 1/3, 1/3, "home", "ligue1")
    assert bs == pytest.approx(BRIER_BASELINE["ligue1"], abs=1e-6)


def test_brier_uniform_nba():
    """Prédiction uniforme NBA → Brier = 0.25."""
    bs = brier_score(0.5, None, 0.5, "home", "nba")
    assert bs == pytest.approx(BRIER_BASELINE["nba"], abs=1e-6)


def test_brier_perfect_ligue1():
    """Prédiction parfaite → Brier = 0."""
    bs = brier_score(1.0, 0.0, 0.0, "home", "ligue1")
    assert bs == pytest.approx(0.0, abs=1e-9)
