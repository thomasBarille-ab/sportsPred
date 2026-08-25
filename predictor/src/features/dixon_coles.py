"""Modèle Dixon-Coles (1997) pour la Ligue 1.

Modélise les scores comme deux Poisson corrélés avec une correction rho
pour les scores faibles (0-0, 1-0, 0-1, 1-1).

Paramètres estimés :
  alpha[i] : force offensive de l'équipe i  (en log-espace → toujours > 0)
  beta[i]  : force défensive de l'équipe i  (en log-espace → toujours > 0)
  gamma    : avantage domicile (en log-espace → toujours > 0)
  rho      : correction corrélation scores faibles (libre, typiquement < 0)

Contrainte d'identifiabilité : sum(log_alpha) = 0 (fixé à la première équipe).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson


# ─────────────────────────────────────────────────────────────────────────────
# Correction Dixon-Coles
# ─────────────────────────────────────────────────────────────────────────────

def _tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0:
        return max(1e-10, 1.0 - lam * mu * rho)
    elif x == 0 and y == 1:
        return 1.0 + lam * rho
    elif x == 1 and y == 0:
        return 1.0 + mu * rho
    elif x == 1 and y == 1:
        return max(1e-10, 1.0 - rho)
    return 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Résultat du modèle
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DCModel:
    teams: list[str]
    attack: dict[str, float]    # attack[team] > 0
    defense: dict[str, float]   # defense[team] > 0
    home_advantage: float
    rho: float
    converged: bool = True

    def predict_goals(self, home_team: str, away_team: str) -> tuple[float, float]:
        """Retourne (lambda_home, mu_away) : buts attendus."""
        a_h = self.attack.get(home_team, 1.0)
        d_h = self.defense.get(home_team, 1.0)
        a_a = self.attack.get(away_team, 1.0)
        d_a = self.defense.get(away_team, 1.0)
        lam = a_h * d_a * self.home_advantage
        mu  = a_a * d_h
        return lam, mu

    def predict_proba(
        self,
        home_team: str,
        away_team: str,
        max_goals: int = 10,
    ) -> tuple[float, float, float]:
        """Retourne (p_home_win, p_draw, p_away_win)."""
        lam, mu = self.predict_goals(home_team, away_team)
        p_home = p_draw = p_away = 0.0

        for x in range(max_goals + 1):
            for y in range(max_goals + 1):
                p = (_tau(x, y, lam, mu, self.rho)
                     * poisson.pmf(x, lam)
                     * poisson.pmf(y, mu))
                if x > y:
                    p_home += p
                elif x == y:
                    p_draw += p
                else:
                    p_away += p

        total = p_home + p_draw + p_away
        if total < 1e-9:
            return 1/3, 1/3, 1/3
        return p_home / total, p_draw / total, p_away / total


# ─────────────────────────────────────────────────────────────────────────────
# Entraînement
# ─────────────────────────────────────────────────────────────────────────────

def _time_weights(dates: list, half_life_days: float = 365.0) -> np.ndarray:
    if not dates:
        return np.array([])
    ref = max(dates)
    lam = math.log(2) / half_life_days
    ages = np.array([(ref - d).days for d in dates], dtype=float)
    return np.exp(-lam * ages)


def _neg_log_likelihood(
    params: np.ndarray,
    teams: list[str],
    home_idx: list[int],
    away_idx: list[int],
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    weights: np.ndarray,
) -> float:
    n = len(teams)
    # log_attack[0] fixé à 0 (contrainte d'identifiabilité)
    log_attack  = np.concatenate([[0.0], params[:n - 1]])
    log_defense = params[n - 1 : 2 * n - 1]
    log_gamma   = params[2 * n - 1]
    rho         = params[2 * n]

    attack  = np.exp(log_attack)
    defense = np.exp(log_defense)
    gamma   = math.exp(log_gamma)

    total = 0.0
    for i in range(len(home_idx)):
        hi, ai = home_idx[i], away_idx[i]
        lam = attack[hi] * defense[ai] * gamma
        mu  = attack[ai] * defense[hi]
        x   = int(home_goals[i])
        y   = int(away_goals[i])
        t   = _tau(x, y, lam, mu, rho)
        if t <= 0:
            t = 1e-10
        ll = (math.log(t)
              + poisson.logpmf(x, lam)
              + poisson.logpmf(y, mu))
        total += weights[i] * ll

    return -total


def fit_dixon_coles(
    matches: list[dict],
    half_life_days: float = 365.0,
) -> DCModel:
    """Entraîne le modèle Dixon-Coles sur une liste de matchs terminés.

    Chaque match dict : home_team_id, away_team_id, home_score, away_score, match_date.
    """
    finished = [m for m in matches if m.get("home_score") is not None]
    if len(finished) < 30:
        # Pas assez de données — retourne un modèle vide (prédictions uniformes)
        return DCModel(teams=[], attack={}, defense={}, home_advantage=1.3, rho=-0.1, converged=False)

    teams = sorted({m["home_team_id"] for m in finished} | {m["away_team_id"] for m in finished})
    team_idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    home_idx   = [team_idx[m["home_team_id"]] for m in finished]
    away_idx   = [team_idx[m["away_team_id"]] for m in finished]
    home_goals = np.array([m["home_score"] for m in finished], dtype=float)
    away_goals = np.array([m["away_score"] for m in finished], dtype=float)
    dates      = [m["match_date"] for m in finished]
    weights    = _time_weights(dates, half_life_days)

    # Initialisation : log_attack=0, log_defense=0, log_gamma=log(1.3), rho=-0.1
    x0 = np.zeros(2 * n)          # n-1 log_attacks + n log_defenses
    x0 = np.zeros(2 * n - 1 + 2)  # (n-1) + n + 1 (gamma) + 1 (rho)
    x0[-2] = math.log(1.3)
    x0[-1] = -0.1

    result = minimize(
        _neg_log_likelihood,
        x0,
        args=(teams, home_idx, away_idx, home_goals, away_goals, weights),
        method="L-BFGS-B",
        options={"maxiter": 500, "ftol": 1e-9},
    )

    params = result.x
    log_attack  = np.concatenate([[0.0], params[: n - 1]])
    log_defense = params[n - 1 : 2 * n - 1]

    attack  = dict(zip(teams, np.exp(log_attack).tolist()))
    defense = dict(zip(teams, np.exp(log_defense).tolist()))

    return DCModel(
        teams=teams,
        attack=attack,
        defense=defense,
        home_advantage=math.exp(float(params[2 * n - 1])),
        rho=float(params[2 * n]),
        converged=result.success,
    )
