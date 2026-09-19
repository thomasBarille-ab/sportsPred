"""Modèle Dixon-Coles (1997) pour la Ligue 1.

Modélise les scores comme deux Poisson corrélés avec une correction rho
pour les scores faibles (0-0, 1-0, 0-1, 1-1).

Paramètres estimés :
  alpha[i] : force offensive de l'équipe i  (en log-espace → toujours > 0)
  beta[i]  : force défensive de l'équipe i  (en log-espace → toujours > 0)
  gamma    : avantage domicile (en log-espace → toujours > 0)
  rho      : correction corrélation scores faibles (libre, typiquement < 0)

Contrainte d'identifiabilité : attack[première équipe] = 1 (log_attack[0] = 0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
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
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    weights: np.ndarray,
) -> float:
    n = len(teams)
    # log_attack[0] fixé à 0 (contrainte d'identifiabilité : attack[première équipe] = 1)
    log_attack  = np.concatenate([[0.0], params[:n - 1]])
    log_defense = params[n - 1 : 2 * n - 1]
    log_gamma   = params[2 * n - 1]
    rho         = params[2 * n]

    attack  = np.exp(log_attack)
    defense = np.exp(log_defense)
    gamma   = math.exp(log_gamma)

    lam = attack[home_idx] * defense[away_idx] * gamma
    mu  = attack[away_idx] * defense[home_idx]
    x   = home_goals
    y   = away_goals

    # Tau vectorisé via np.where
    tau = np.where(
        (x == 0) & (y == 0), np.maximum(1e-10, 1.0 - lam * mu * rho),
        np.where(
            (x == 0) & (y == 1), 1.0 + lam * rho,
            np.where(
                (x == 1) & (y == 0), 1.0 + mu * rho,
                np.where(
                    (x == 1) & (y == 1), np.maximum(1e-10, 1.0 - rho),
                    1.0,
                ),
            ),
        ),
    )
    tau = np.maximum(tau, 1e-10)

    # Log-PMF Poisson : x*log(lam) - lam - gammaln(x+1)
    log_p_home = x * np.log(np.maximum(lam, 1e-10)) - lam - gammaln(x + 1)
    log_p_away = y * np.log(np.maximum(mu, 1e-10)) - mu  - gammaln(y + 1)

    ll = np.log(tau) + log_p_home + log_p_away
    return -float(np.dot(weights, ll))


def dc_model_to_x0(model: DCModel, teams: list[str]) -> np.ndarray | None:
    """Extrait le vecteur de paramètres d'un DCModel pour le warm start.

    Retourne None si les équipes ont changé depuis le dernier fit.
    """
    if not model.converged or not model.teams:
        return None
    if set(teams) != set(model.teams):
        return None
    sorted_teams = sorted(teams)
    log_attacks  = [math.log(max(model.attack.get(t, 1.0), 1e-10)) for t in sorted_teams]
    log_defenses = [math.log(max(model.defense.get(t, 1.0), 1e-10)) for t in sorted_teams]
    return np.array(
        log_attacks[1:]           # n-1
        + log_defenses            # n
        + [math.log(max(model.home_advantage, 1e-10))]  # 1
        + [model.rho],            # 1
        dtype=float,
    )


def fit_dixon_coles(
    matches: list[dict],
    half_life_days: float = 365.0,
    x0: np.ndarray | None = None,
) -> DCModel:
    """Entraîne le modèle Dixon-Coles sur une liste de matchs terminés.

    Chaque match dict : home_team_id, away_team_id, home_score, away_score, match_date.
    x0 : point de départ optionnel pour l'optimiseur (warm start depuis un fit précédent).
    """
    finished = [m for m in matches if m.get("home_score") is not None]
    if len(finished) < 30:
        return DCModel(teams=[], attack={}, defense={}, home_advantage=1.3, rho=-0.1, converged=False)

    teams = sorted({m["home_team_id"] for m in finished} | {m["away_team_id"] for m in finished})
    team_idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    home_idx   = np.array([team_idx[m["home_team_id"]] for m in finished], dtype=int)
    away_idx   = np.array([team_idx[m["away_team_id"]] for m in finished], dtype=int)
    home_goals = np.array([m["home_score"] for m in finished], dtype=float)
    away_goals = np.array([m["away_score"] for m in finished], dtype=float)
    dates      = [m["match_date"] for m in finished]
    weights    = _time_weights(dates, half_life_days)

    param_len = 2 * n - 1 + 2  # (n-1) log_attacks + n log_defenses + log_gamma + rho
    default_x0 = np.zeros(param_len)
    default_x0[-2] = math.log(1.3)
    default_x0[-1] = -0.1

    init_x0 = x0 if (x0 is not None and len(x0) == param_len) else default_x0

    result = minimize(
        _neg_log_likelihood,
        init_x0,
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
