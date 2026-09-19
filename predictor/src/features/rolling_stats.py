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


def days_since_last_scheduled_game(
    team_id: str,
    match_date: datetime,
    schedule: list[dict],
) -> float:
    """Jours depuis le dernier match planifié (hors CANCELLED/POSTPONED) avant match_date.

    `schedule` : toutes les fixtures avec au minimum home_team_id, away_team_id, match_date,
    et optionnellement status. Utilisé pour calculer le repos réel depuis le calendrier.
    """
    candidates = [
        m for m in schedule
        if (m["home_team_id"] == team_id or m["away_team_id"] == team_id)
        and m["match_date"] < match_date
        and m.get("status") not in ("CANCELLED", "POSTPONED")
    ]
    if not candidates:
        return 30.0
    last = max(m["match_date"] for m in candidates)
    return float((match_date - last).total_seconds() / 86400)


def is_back2back_from_schedule(
    team_id: str,
    match_date: datetime,
    schedule: list[dict],
) -> float:
    """1.0 si le dernier match planifié était il y a ≤ 36 h, sinon 0.0."""
    days = days_since_last_scheduled_game(team_id, match_date, schedule)
    return 1.0 if days <= 1.5 else 0.0


def compute_rolling_stats(
    team_id: str,
    match_date: datetime,
    all_matches: list[dict],
    window: int = 10,
    schedule: list[dict] | None = None,
) -> dict[str, float]:
    """Statistiques glissantes sur `window` matchs terminés avant `match_date`.

    `schedule` : si fourni, utilisé pour is_back2back et days_since à la place de
    all_matches (permet d'inclure les matchs planifiés non encore joués).
    Par défaut, all_matches est utilisé pour les deux (comportement inchangé).

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

    # La forme (ppg, win_rate) est toujours calculée depuis les matchs TERMINÉS
    games = _team_games(team_id, all_matches, before=match_date)

    # Le B2B et le repos se calculent depuis le calendrier complet si fourni
    cal = schedule if schedule is not None else all_matches
    is_b2b = is_back2back_from_schedule(team_id, match_date, cal)

    if not games:
        return {**defaults, "is_back2back": is_b2b}

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
        # _outcome gère déjà le point de vue de team_id (home ou away)
        w = _outcome(team_id, m)
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
