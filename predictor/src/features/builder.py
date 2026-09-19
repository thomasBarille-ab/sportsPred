"""Construit le vecteur de features pour un match donné.

Ligue 1 features :
  elo_home, elo_away, elo_diff,
  dc_lam (expected home goals), dc_mu (expected away goals),
  dc_p_home, dc_p_draw, dc_p_away,
  home_ppg_last5, home_conceded_last5, home_form_pts_last5,
  away_ppg_last5, away_conceded_last5, away_form_pts_last5,
  h2h_home_wins, h2h_draws, h2h_away_wins,
  days_since_home_game, days_since_away_game

NBA features :
  elo_home, elo_away, elo_diff,
  home_ppg_last10, home_oppg_last10, home_win_rate_last10, home_b2b,
  away_ppg_last10, away_oppg_last10, away_win_rate_last10, away_b2b,
  h2h_home_wins, h2h_away_wins,
  days_since_home_game, days_since_away_game
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np

from .dixon_coles import DCModel
from .elo import EloState
from .rolling_stats import (
    compute_h2h_stats,
    compute_rolling_stats,
    days_since_last_scheduled_game,
)

# Feature names dans l'ordre exact attendu par XGBoost
LIGUE1_FEATURES = [
    "elo_home", "elo_away", "elo_diff",
    "dc_lam", "dc_mu",
    "dc_p_home", "dc_p_draw", "dc_p_away",
    "home_ppg_last5", "home_conceded_last5", "home_form_pts_last5",
    "away_ppg_last5", "away_conceded_last5", "away_form_pts_last5",
    "h2h_home_wins", "h2h_draws", "h2h_away_wins",
    "days_since_home_game", "days_since_away_game",
]

NBA_FEATURES = [
    "elo_home", "elo_away", "elo_diff",
    "home_ppg_last10", "home_oppg_last10", "home_win_rate_last10", "home_b2b",
    "away_ppg_last10", "away_oppg_last10", "away_win_rate_last10", "away_b2b",
    "h2h_home_wins", "h2h_away_wins",
    "days_since_home_game", "days_since_away_game",
]


def _days_since_last_game(
    team_id: str,
    match_date: datetime,
    all_matches: list[dict],
    schedule: list[dict] | None = None,
) -> float:
    """Jours depuis le dernier match.

    Si `schedule` est fourni, utilise le calendrier complet (hors CANCELLED/POSTPONED)
    pour refléter le vrai repos — y compris les matchs planifiés non encore joués.
    Sinon, ne considère que les matchs terminés (comportement original).
    """
    if schedule is not None:
        return days_since_last_scheduled_game(team_id, match_date, schedule)
    games = [
        m for m in all_matches
        if (m["home_team_id"] == team_id or m["away_team_id"] == team_id)
        and m["match_date"] < match_date
        and m.get("home_score") is not None
    ]
    if not games:
        return 30.0
    last = max(m["match_date"] for m in games)
    return float((match_date - last).days)


def _rolling_pts_last5(team_id: str, match_date: datetime, all_matches: list[dict]) -> tuple[float, float, float]:
    """Retourne (ppg_scored, ppg_conceded, form_points) sur les 5 derniers matchs (foot)."""
    games_foot = [
        m for m in all_matches
        if (m["home_team_id"] == team_id or m["away_team_id"] == team_id)
        and m["match_date"] < match_date
        and m.get("home_score") is not None
    ]
    games_foot = sorted(games_foot, key=lambda x: x["match_date"], reverse=True)[:5]
    if not games_foot:
        return 1.3, 1.3, 5.0

    scored = conceded = pts = 0.0
    for m in games_foot:
        is_home = m["home_team_id"] == team_id
        gs = m["home_score"] if is_home else m["away_score"]
        gc = m["away_score"] if is_home else m["home_score"]
        scored += gs
        conceded += gc
        if gs > gc: pts += 3
        elif gs == gc: pts += 1

    n = len(games_foot)
    return scored / n, conceded / n, pts


def build_features_ligue1(
    home_team_id: str,
    away_team_id: str,
    match_date: datetime,
    all_matches: list[dict],
    elo_state: EloState,
    dc_model: DCModel,
    schedule: list[dict] | None = None,
) -> tuple[list[float], list[str]]:
    """Retourne (feature_vector, feature_names).

    `schedule` : calendrier complet (fixtures planifiées, hors CANCELLED/POSTPONED)
    pour le calcul du repos. Par défaut utilise all_matches (matchs terminés seulement).
    """
    elo_h = elo_state.get(home_team_id)
    elo_a = elo_state.get(away_team_id)

    lam, mu = (1.4, 1.1)
    p_home, p_draw, p_away = 1/3, 1/3, 1/3
    if home_team_id in dc_model.attack and away_team_id in dc_model.attack:
        lam, mu = dc_model.predict_goals(home_team_id, away_team_id)
        p_home, p_draw, p_away = dc_model.predict_proba(home_team_id, away_team_id)

    h_ppg, h_con, h_pts = _rolling_pts_last5(home_team_id, match_date, all_matches)
    a_ppg, a_con, a_pts = _rolling_pts_last5(away_team_id, match_date, all_matches)
    h2h = compute_h2h_stats(home_team_id, away_team_id, match_date, all_matches, window=5)

    days_h = _days_since_last_game(home_team_id, match_date, all_matches, schedule)
    days_a = _days_since_last_game(away_team_id, match_date, all_matches, schedule)

    vec = [
        elo_h, elo_a, elo_h - elo_a,
        lam, mu,
        p_home, p_draw, p_away,
        h_ppg, h_con, h_pts,
        a_ppg, a_con, a_pts,
        h2h["h2h_home_wins"], h2h["h2h_draws"], h2h["h2h_away_wins"],
        days_h, days_a,
    ]
    return vec, LIGUE1_FEATURES


def build_features_nba(
    home_team_id: str,
    away_team_id: str,
    match_date: datetime,
    all_matches: list[dict],
    elo_state: EloState,
    schedule: list[dict] | None = None,
) -> tuple[list[float], list[str]]:
    """Retourne (feature_vector, feature_names).

    `schedule` : calendrier complet pour le calcul du repos/B2B.
    Par défaut utilise all_matches.
    """
    elo_h = elo_state.get(home_team_id)
    elo_a = elo_state.get(away_team_id)

    h_stats = compute_rolling_stats(home_team_id, match_date, all_matches, window=10, schedule=schedule)
    a_stats = compute_rolling_stats(away_team_id, match_date, all_matches, window=10, schedule=schedule)
    h2h = compute_h2h_stats(home_team_id, away_team_id, match_date, all_matches, window=5)
    days_h = _days_since_last_game(home_team_id, match_date, all_matches, schedule)
    days_a = _days_since_last_game(away_team_id, match_date, all_matches, schedule)

    vec = [
        elo_h, elo_a, elo_h - elo_a,
        h_stats["ppg"], h_stats["oppg"], h_stats["win_rate"], h_stats["is_back2back"],
        a_stats["ppg"], a_stats["oppg"], a_stats["win_rate"], a_stats["is_back2back"],
        h2h["h2h_home_wins"], h2h["h2h_away_wins"],
        days_h, days_a,
    ]
    return vec, NBA_FEATURES
