"""Statistiques glissantes pour la NBA.

Pour chaque équipe, calcule sur les N derniers matchs :
  - points marqués (PPG) et encaissés (OPPG)
  - win rate
  - back-to-back indicator (match précédent < 2 jours)

Les stats sont calculées *avant* la date du match cible pour éviter
tout data leakage.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


def _outcome(home_id: str, m: dict) -> Optional[float]:
    """1 = win, 0 = loss, None = not finished."""
    if m.get("home_score") is None:
        return None
    if m["home_team_id"] == home_id:
        if m["home_score"] > m["away_score"]:
            return 1.0
        elif m["home_score"] < m["away_score"]:
            return 0.0
        return 0.5
    else:
        if m["away_score"] > m["home_score"]:
            return 1.0
        elif m["away_score"] < m["home_score"]:
            return 0.0
        return 0.5


def _team_games(team_id: str, matches: list[dict], before: datetime) -> list[dict]:
    """Matchs terminés d'une équipe avant une date donnée, triés du plus récent au plus ancien."""
    games = [
        m for m in matches
        if (m["home_team_id"] == team_id or m["away_team_id"] == team_id)
        and m["match_date"] < before
        and m.get("home_score") is not None
    ]
    return sorted(games, key=lambda x: x["match_date"], reverse=True)


def compute_rolling_stats(
    team_id: str,
    match_date: datetime,
    all_matches: list[dict],
    window: int = 10,
) -> dict[str, float]:
    """Statistiques glissantes sur `window` matchs avant `match_date`.

    Retourne un dict de features avec des valeurs par défaut neutres si
    pas assez de données.
    """
    defaults = {
        "ppg": 110.0,
        "oppg": 110.0,
        "win_rate": 0.5,
        "is_back2back": 0.0,
        "n_games": 0.0,
    }

    games = _team_games(team_id, all_matches, before=match_date)
    if not games:
        return defaults

    # Back-to-back : le match précédent était hier ou avant-hier
    last_game_date = games[0]["match_date"]
    is_b2b = 1.0 if (match_date - last_game_date) <= timedelta(days=1, hours=12) else 0.0

    recent = games[:window]
    n = len(recent)

    pts_scored = []
    pts_allowed = []
    wins = []

    for m in recent:
        is_home = m["home_team_id"] == team_id
        scored  = m["home_score"] if is_home else m["away_score"]
        allowed = m["away_score"] if is_home else m["home_score"]
        pts_scored.append(scored)
        pts_allowed.append(allowed)
        w = _outcome(team_id if is_home else m["away_team_id"], m)
        if not is_home:
            # flip
            w = 1.0 - w if w is not None else None
        wins.append(w if w is not None else 0.5)

    return {
        "ppg": sum(pts_scored) / n,
        "oppg": sum(pts_allowed) / n,
        "win_rate": sum(wins) / n,
        "is_back2back": is_b2b,
        "n_games": float(n),
    }


def compute_h2h_stats(
    home_id: str,
    away_id: str,
    match_date: datetime,
    all_matches: list[dict],
    window: int = 5,
) -> dict[str, float]:
    """Head-to-head entre deux équipes avant `match_date`."""
    h2h = [
        m for m in all_matches
        if (
            (m["home_team_id"] == home_id and m["away_team_id"] == away_id)
            or (m["home_team_id"] == away_id and m["away_team_id"] == home_id)
        )
        and m["match_date"] < match_date
        and m.get("home_score") is not None
    ]
    h2h = sorted(h2h, key=lambda x: x["match_date"], reverse=True)[:window]

    if not h2h:
        return {"h2h_home_wins": 0.0, "h2h_draws": 0.0, "h2h_away_wins": 0.0, "h2h_n": 0.0}

    home_wins = draws = away_wins = 0
    for m in h2h:
        hs = m["home_score"]
        as_ = m["away_score"]
        # Normalise par rapport au home_id
        actual_home = m["home_team_id"] == home_id
        if actual_home:
            if hs > as_: home_wins += 1
            elif hs == as_: draws += 1
            else: away_wins += 1
        else:
            if as_ > hs: home_wins += 1
            elif hs == as_: draws += 1
            else: away_wins += 1

    n = len(h2h)
    return {
        "h2h_home_wins": home_wins / n,
        "h2h_draws": draws / n,
        "h2h_away_wins": away_wins / n,
        "h2h_n": float(n),
    }
