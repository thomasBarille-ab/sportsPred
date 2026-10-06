"""Construit le vecteur de features pour un match donné.

Ligue 1 features (33) :
  elo_home, elo_away, elo_diff,
  dc_lam, dc_mu,
  dc_p_home, dc_p_draw, dc_p_away,
  home_ppg_last5, home_conceded_last5, home_form_pts_last5,
  away_ppg_last5, away_conceded_last5, away_form_pts_last5,
  h2h_home_wins, h2h_draws, h2h_away_wins,
  days_since_home_game, days_since_away_game,
  home_xg_last5, home_xga_last5, away_xg_last5, away_xga_last5,  [Understat]
  implied_prob_home, implied_prob_draw, implied_prob_away, market_efficiency  [bookmaker]
  home_injuries_count, away_injuries_count,  [agent contexte]
  home_rotation_signal, away_rotation_signal,
  weather_rain_mm, weather_wind_kmh

NBA features (21) :
  elo_home, elo_away, elo_diff,
  home_ortg, home_drtg, home_net_rtg, home_win_rate_last10, home_b2b,
  away_ortg, away_drtg, away_net_rtg, away_win_rate_last10, away_b2b,
  h2h_home_wins, h2h_away_wins,
  days_since_home_game, days_since_away_game,
  implied_prob_home, implied_prob_away, market_efficiency  [bookmaker]

France NT features (34) :
  elo_home, elo_away, elo_diff,
  dc_lam, dc_mu,
  dc_p_home, dc_p_draw, dc_p_away,
  home_ppg_last5, home_conceded_last5, home_form_pts_last5,
  away_ppg_last5, away_conceded_last5, away_form_pts_last5,
  h2h_home_wins, h2h_draws, h2h_away_wins,
  days_since_home_match, days_since_away_match,
  competition_type,          [0=friendly 1=nations_league 2=qualifier 3=tournament]
  france_is_home,            [1 si France = équipe domicile]
  manager_id,                [0=Deschamps, à incrémenter lors d'un changement]
  n_starters_absent,         [absents vs dernier XI type]
  starters_available_ratio,  [défaut 1.0 sans données de dispo]
  implied_prob_home, implied_prob_draw, implied_prob_away, market_efficiency  [bookmaker]
  home_injuries_count, away_injuries_count,  [agent contexte]
  home_rotation_signal, away_rotation_signal,
  weather_rain_mm, weather_wind_kmh
"""

from __future__ import annotations

from datetime import datetime

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
    "home_xg_last5", "home_xga_last5", "away_xg_last5", "away_xga_last5",
    "implied_prob_home", "implied_prob_draw", "implied_prob_away", "market_efficiency",
    "home_injuries_count", "away_injuries_count",
    "home_rotation_signal", "away_rotation_signal",
    "weather_rain_mm", "weather_wind_kmh",
]

NBA_FEATURES = [
    "elo_home", "elo_away", "elo_diff",
    "home_ortg", "home_drtg", "home_net_rtg", "home_win_rate_last10", "home_b2b",
    "away_ortg", "away_drtg", "away_net_rtg", "away_win_rate_last10", "away_b2b",
    "h2h_home_wins", "h2h_away_wins",
    "days_since_home_game", "days_since_away_game",
    "implied_prob_home", "implied_prob_away", "market_efficiency",
]

FRANCE_NT_FEATURES = [
    "elo_home", "elo_away", "elo_diff",
    "dc_lam", "dc_mu",
    "dc_p_home", "dc_p_draw", "dc_p_away",
    "home_ppg_last5", "home_conceded_last5", "home_form_pts_last5",
    "away_ppg_last5", "away_conceded_last5", "away_form_pts_last5",
    "h2h_home_wins", "h2h_draws", "h2h_away_wins",
    "days_since_home_match", "days_since_away_match",
    "competition_type",
    "france_is_home",
    "manager_id",
    "n_starters_absent",
    "starters_available_ratio",
    "implied_prob_home", "implied_prob_draw", "implied_prob_away", "market_efficiency",
    "home_injuries_count", "away_injuries_count",
    "home_rotation_signal", "away_rotation_signal",
    "weather_rain_mm", "weather_wind_kmh",
]

# France NT team ID (API-Football)
FRANCE_TEAM_ID = "2"

# Codes de compétition → entier pour le feature competition_type
_COMPETITION_TYPE: dict[str, float] = {
    "FRIENDLY":  0.0,
    "UEFA_UNL":  1.0,
    "FIFA_WCQ":  2.0,
    "UEFA_ECQ":  2.0,
    "FIFA_WC":   3.0,
    "UEFA_EC":   3.0,
}


def _competition_type_value(competition: str) -> float:
    return _COMPETITION_TYPE.get(competition, 0.0)


def _manager_id_france(match_date: datetime) -> float:
    # Deschamps en poste depuis juillet 2012 — couvre toute la période 2018+.
    # Incrémenter manuellement lors d'un changement de sélectionneur.
    return 0.0


def _days_since_last_game(
    team_id: str,
    match_date: datetime,
    all_matches: list[dict],
    schedule: list[dict] | None = None,
) -> float:
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
    games = [
        m for m in all_matches
        if (m["home_team_id"] == team_id or m["away_team_id"] == team_id)
        and m["match_date"] < match_date
        and m.get("home_score") is not None
    ]
    games = sorted(games, key=lambda x: x["match_date"], reverse=True)[:5]
    if not games:
        return 1.3, 1.3, 5.0

    scored = conceded = pts = 0.0
    for m in games:
        is_home = m["home_team_id"] == team_id
        gs = m["home_score"] if is_home else m["away_score"]
        gc = m["away_score"] if is_home else m["home_score"]
        scored += gs
        conceded += gc
        if gs > gc: pts += 3
        elif gs == gc: pts += 1

    n = len(games)
    return scored / n, conceded / n, pts


def _rolling_xg_last5(
    team_id: str,
    match_date: datetime,
    all_matches: list[dict],
) -> tuple[float, float]:
    """Retourne (xg_scored_avg, xg_conceded_avg) sur les 5 derniers matchs.

    Utilise home_xg / away_xg depuis all_matches. Retourne np.nan si aucune donnée.
    """
    games = [
        m for m in all_matches
        if (m["home_team_id"] == team_id or m["away_team_id"] == team_id)
        and m["match_date"] < match_date
        and m.get("home_score") is not None
        and m.get("home_xg") is not None
        and m.get("away_xg") is not None
    ]
    games = sorted(games, key=lambda x: x["match_date"], reverse=True)[:5]
    if not games:
        return float("nan"), float("nan")

    xg_scored = xg_conceded = 0.0
    for m in games:
        is_home = m["home_team_id"] == team_id
        xg_scored   += float(m["home_xg"] if is_home else m["away_xg"])
        xg_conceded += float(m["away_xg"] if is_home else m["home_xg"])

    n = len(games)
    return xg_scored / n, xg_conceded / n


def _implied_probs_ligue1(
    odds_home: float | None,
    odds_draw: float | None,
    odds_away: float | None,
) -> tuple[float, float, float, float]:
    """Retourne (implied_prob_home, implied_prob_draw, implied_prob_away, market_efficiency).

    market_efficiency = overround (somme des probabilités brutes avant normalisation).
    Un overround de 1.05 = marge bookmaker de 5 %.
    Retourne nan si cotes absentes ou invalides.
    """
    if odds_home and odds_draw and odds_away and odds_home > 1 and odds_draw > 1 and odds_away > 1:
        inv_h = 1.0 / odds_home
        inv_d = 1.0 / odds_draw
        inv_a = 1.0 / odds_away
        overround = inv_h + inv_d + inv_a
        return inv_h / overround, inv_d / overround, inv_a / overround, overround
    return float("nan"), float("nan"), float("nan"), float("nan")


def _implied_probs_nba(
    odds_home: float | None,
    odds_away: float | None,
) -> tuple[float, float, float]:
    """Retourne (implied_prob_home, implied_prob_away, market_efficiency)."""
    if odds_home and odds_away and odds_home > 1 and odds_away > 1:
        inv_h = 1.0 / odds_home
        inv_a = 1.0 / odds_away
        overround = inv_h + inv_a
        return inv_h / overround, inv_a / overround, overround
    return float("nan"), float("nan"), float("nan")


def build_features_ligue1(
    home_team_id: str,
    away_team_id: str,
    match_date: datetime,
    all_matches: list[dict],
    elo_state: EloState,
    dc_model: DCModel,
    schedule: list[dict] | None = None,
    odds_home: float | None = None,
    odds_draw: float | None = None,
    odds_away: float | None = None,
    context: dict | None = None,
) -> tuple[list[float], list[str]]:
    """Retourne (feature_vector, feature_names).

    odds_* : cotes décimales du bookmaker le plus sharp disponible (Pinnacle > Bet365).
             None si pas de cotes en base → NaN dans le vecteur (XGBoost gère nativement).
    context : dict issu de match_context (injuries, rotation, weather). None → valeurs neutres.
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

    h_xg, h_xga = _rolling_xg_last5(home_team_id, match_date, all_matches)
    a_xg, a_xga = _rolling_xg_last5(away_team_id, match_date, all_matches)

    imp_h, imp_d, imp_a, overround = _implied_probs_ligue1(odds_home, odds_draw, odds_away)

    _nan = float("nan")
    home_inj = float(context["home_injuries_count"]) if context and "home_injuries_count" in context else _nan
    away_inj = float(context["away_injuries_count"]) if context and "away_injuries_count" in context else _nan
    home_rot = float(context["home_rotation_signal"]) if context and "home_rotation_signal" in context else _nan
    away_rot = float(context["away_rotation_signal"]) if context and "away_rotation_signal" in context else _nan
    rain_mm  = float(context.get("weather_rain_mm", 0.0)) if context else 0.0
    wind_kmh = float(context.get("weather_wind_kmh", 0.0)) if context else 0.0

    vec = [
        elo_h, elo_a, elo_h - elo_a,
        lam, mu,
        p_home, p_draw, p_away,
        h_ppg, h_con, h_pts,
        a_ppg, a_con, a_pts,
        h2h["h2h_home_wins"], h2h["h2h_draws"], h2h["h2h_away_wins"],
        days_h, days_a,
        h_xg, h_xga, a_xg, a_xga,
        imp_h, imp_d, imp_a, overround,
        home_inj, away_inj,
        home_rot, away_rot,
        rain_mm, wind_kmh,
    ]
    return vec, LIGUE1_FEATURES


def build_features_nba(
    home_team_id: str,
    away_team_id: str,
    match_date: datetime,
    all_matches: list[dict],
    elo_state: EloState,
    schedule: list[dict] | None = None,
    odds_home: float | None = None,
    odds_away: float | None = None,
) -> tuple[list[float], list[str]]:
    """Retourne (feature_vector, feature_names).

    odds_* : cotes décimales. None → NaN.
    """
    elo_h = elo_state.get(home_team_id)
    elo_a = elo_state.get(away_team_id)

    h_stats = compute_rolling_stats(home_team_id, match_date, all_matches, window=10, schedule=schedule)
    a_stats = compute_rolling_stats(away_team_id, match_date, all_matches, window=10, schedule=schedule)
    h2h = compute_h2h_stats(home_team_id, away_team_id, match_date, all_matches, window=5)
    days_h = _days_since_last_game(home_team_id, match_date, all_matches, schedule)
    days_a = _days_since_last_game(away_team_id, match_date, all_matches, schedule)

    imp_h, imp_a, overround = _implied_probs_nba(odds_home, odds_away)

    vec = [
        elo_h, elo_a, elo_h - elo_a,
        h_stats["ortg"], h_stats["drtg"], h_stats["net_rtg"], h_stats["win_rate"], h_stats["is_back2back"],
        a_stats["ortg"], a_stats["drtg"], a_stats["net_rtg"], a_stats["win_rate"], a_stats["is_back2back"],
        h2h["h2h_home_wins"], h2h["h2h_away_wins"],
        days_h, days_a,
        imp_h, imp_a, overround,
    ]
    return vec, NBA_FEATURES


def build_features_france_nt(
    home_team_id: str,
    away_team_id: str,
    match_date: datetime,
    competition: str,
    all_matches: list[dict],
    elo_state: EloState,
    dc_model: DCModel | None = None,
    n_starters_absent: int = 0,
    starters_available_ratio: float = 1.0,
    odds_home: float | None = None,
    odds_draw: float | None = None,
    odds_away: float | None = None,
    context: dict | None = None,
) -> tuple[list[float], list[str]]:
    """Features pour l'Équipe de France NT (34 features, 3 classes).

    home_team_id / away_team_id : tels que stockés en DB (API-Football IDs).
    competition : code interne (FRIENDLY, UEFA_UNL, FIFA_WCQ, FIFA_WC, UEFA_EC, UEFA_ECQ).
    dc_model : modèle Dixon-Coles entraîné sur les matchs passés (None = valeurs par défaut).
    n_starters_absent : calculé depuis france_nt_lineups avant appel (défaut 0).
    starters_available_ratio : disponibles / 11 (défaut 1.0).
    context : dict issu de match_context (injuries, rotation, weather). None → valeurs neutres.
    """
    elo_h = elo_state.get(home_team_id)
    elo_a = elo_state.get(away_team_id)

    lam, mu = 1.4, 1.1
    p_home, p_draw, p_away = 1 / 3, 1 / 3, 1 / 3
    if dc_model is not None and home_team_id in dc_model.attack and away_team_id in dc_model.attack:
        lam, mu = dc_model.predict_goals(home_team_id, away_team_id)
        p_home, p_draw, p_away = dc_model.predict_proba(home_team_id, away_team_id)

    h_ppg, h_con, h_pts = _rolling_pts_last5(home_team_id, match_date, all_matches)
    a_ppg, a_con, a_pts = _rolling_pts_last5(away_team_id, match_date, all_matches)
    h2h = compute_h2h_stats(home_team_id, away_team_id, match_date, all_matches, window=5)

    days_h = _days_since_last_game(home_team_id, match_date, all_matches)
    days_a = _days_since_last_game(away_team_id, match_date, all_matches)

    comp_type = _competition_type_value(competition)
    france_is_home = 1.0 if home_team_id == FRANCE_TEAM_ID else 0.0
    manager_id = _manager_id_france(match_date)

    imp_h, imp_d, imp_a, overround = _implied_probs_ligue1(odds_home, odds_draw, odds_away)

    _nan = float("nan")
    home_inj = float(context["home_injuries_count"]) if context and "home_injuries_count" in context else _nan
    away_inj = float(context["away_injuries_count"]) if context and "away_injuries_count" in context else _nan
    home_rot = float(context["home_rotation_signal"]) if context and "home_rotation_signal" in context else _nan
    away_rot = float(context["away_rotation_signal"]) if context and "away_rotation_signal" in context else _nan
    rain_mm  = float(context.get("weather_rain_mm", 0.0)) if context else 0.0
    wind_kmh = float(context.get("weather_wind_kmh", 0.0)) if context else 0.0

    vec = [
        elo_h, elo_a, elo_h - elo_a,
        lam, mu,
        p_home, p_draw, p_away,
        h_ppg, h_con, h_pts,
        a_ppg, a_con, a_pts,
        h2h["h2h_home_wins"], h2h["h2h_draws"], h2h["h2h_away_wins"],
        days_h, days_a,
        comp_type,
        france_is_home,
        manager_id,
        float(n_starters_absent),
        starters_available_ratio,
        imp_h, imp_d, imp_a, overround,
        home_inj, away_inj,
        home_rot, away_rot,
        rain_mm, wind_kmh,
    ]
    return vec, FRANCE_NT_FEATURES
