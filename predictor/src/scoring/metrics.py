"""Calcul des métriques de performance par prédiction."""

from __future__ import annotations

import math
from typing import Literal


Outcome = Literal["home", "draw", "away"]


def outcome_from_scores(home_score: int, away_score: int, sport: str) -> Outcome:
    if home_score > away_score:
        return "home"
    if away_score > home_score:
        return "away"
    if sport == "ligue1":
        return "draw"
    # NBA ne devrait pas avoir de match nul — on force "home" par convention
    return "home"


def brier_score(
    prob_home: float,
    prob_draw: float | None,
    prob_away: float,
    actual: Outcome,
    sport: str,
) -> float:
    """Brier score multi-classe (normalisé entre 0 et 1).

    Plus bas = meilleur. Modèle parfait = 0, aléatoire uniforme ≈ 0.67.
    """
    if sport == "ligue1":
        o_home = 1.0 if actual == "home" else 0.0
        o_draw = 1.0 if actual == "draw" else 0.0
        o_away = 1.0 if actual == "away" else 0.0
        pd = prob_draw or 0.0
        return ((prob_home - o_home) ** 2 + (pd - o_draw) ** 2 + (prob_away - o_away) ** 2) / 2
    else:
        o_home = 1.0 if actual == "home" else 0.0
        return (prob_home - o_home) ** 2


def log_loss_single(
    prob_home: float,
    prob_draw: float | None,
    prob_away: float,
    actual: Outcome,
    sport: str,
    eps: float = 1e-7,
) -> float:
    """Log-loss pour une seule prédiction."""
    prob_home = max(eps, min(1 - eps, prob_home))
    prob_away = max(eps, min(1 - eps, prob_away))

    if sport == "ligue1":
        pd = max(eps, min(1 - eps, prob_draw or 0.0))
        if actual == "home": return -math.log(prob_home)
        if actual == "draw": return -math.log(pd)
        return -math.log(prob_away)
    else:
        if actual == "home": return -math.log(prob_home)
        return -math.log(prob_away)


def score_prediction(
    prob_home: float,
    prob_draw: float | None,
    prob_away: float,
    predicted_outcome: Outcome,
    actual_home_score: int,
    actual_away_score: int,
    sport: str,
) -> dict[str, float | bool]:
    actual = outcome_from_scores(actual_home_score, actual_away_score, sport)
    bs = brier_score(prob_home, prob_draw, prob_away, actual, sport)
    ll = log_loss_single(prob_home, prob_draw, prob_away, actual, sport)
    correct = predicted_outcome == actual
    return {"brier_score": bs, "log_loss": ll, "is_correct": correct}
