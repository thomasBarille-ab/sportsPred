"""Modèle de scoring NBA baseline — ratings offensifs/défensifs EWMA.

Pour chaque équipe :
  off_rating[t] = EWMA des points marqués (half-life 180 jours)
  def_rating[t] = EWMA des points concédés

Prédictions dérivées :
  expected_points(home, away) = ((off_h + def_a)/2 + home_adv/2,
                                 (off_a + def_h)/2 - home_adv/2)
  P(over X)   via Normal(exp_total,  sigma_total ≈ 22)
  P(cover s)  via Normal(exp_margin, sigma_spread ≈ 12)

Les sigmas sont des moyennes empiriques NBA. Un affinage ultérieur depuis
les résidus d'entraînement est trivial à ajouter si besoin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
from scipy.stats import norm

# Valeurs empiriques NBA — raisonnables sur plusieurs saisons récentes.
_HOME_ADVANTAGE_PTS = 2.5
_SIGMA_TOTAL = 22.0
_SIGMA_SPREAD = 12.0
_DEFAULT_LEAGUE_AVG = 115.0


@dataclass
class NBAScoringModel:
    off_rating: dict[str, float] = field(default_factory=dict)
    def_rating: dict[str, float] = field(default_factory=dict)
    league_avg: float = _DEFAULT_LEAGUE_AVG
    home_advantage: float = _HOME_ADVANTAGE_PTS
    sigma_total: float = _SIGMA_TOTAL
    sigma_spread: float = _SIGMA_SPREAD
    converged: bool = True

    def _lookup(self, team: str) -> tuple[float, float]:
        off = self.off_rating.get(team, self.league_avg)
        dfn = self.def_rating.get(team, self.league_avg)
        return off, dfn

    def predict_points(self, home_team: str, away_team: str) -> tuple[float, float]:
        """Retourne (exp_home_pts, exp_away_pts)."""
        oh, dh = self._lookup(home_team)
        oa, da = self._lookup(away_team)
        home_exp = (oh + da) / 2.0 + self.home_advantage / 2.0
        away_exp = (oa + dh) / 2.0 - self.home_advantage / 2.0
        return home_exp, away_exp

    def predict_over_under(
        self,
        home_team: str,
        away_team: str,
        line: float,
    ) -> tuple[float, float]:
        """P(total > line), P(total < line). Les demi-lignes ne poussent jamais."""
        home_exp, away_exp = self.predict_points(home_team, away_team)
        exp_total = home_exp + away_exp
        p_over = float(1.0 - norm.cdf(line, loc=exp_total, scale=self.sigma_total))
        p_over = min(max(p_over, 0.0), 1.0)
        return p_over, 1.0 - p_over

    def predict_spread(
        self,
        home_team: str,
        away_team: str,
        selection: str,
        line: float,
    ) -> float:
        """P(selection cover). line = point tel que renvoyé par l'API (home_line pour home).

        home cover ⇔ margin > -line    (margin = home_score - away_score)
        away cover ⇔ margin <  line    (-margin > -line_away et line_away = -line_home)
        """
        home_exp, away_exp = self.predict_points(home_team, away_team)
        exp_margin = home_exp - away_exp
        if selection == "home":
            p = 1.0 - norm.cdf(-line, loc=exp_margin, scale=self.sigma_spread)
        else:  # away
            p = norm.cdf(line, loc=exp_margin, scale=self.sigma_spread)
        return float(min(max(p, 0.0), 1.0))


def _days_between(a: date | datetime, b: date | datetime) -> float:
    """Nombre de jours entre deux dates, robuste aux types mélangés."""
    def _to_date(d: date | datetime) -> date:
        return d.date() if isinstance(d, datetime) else d
    return float((_to_date(a) - _to_date(b)).days)


def fit_nba_scoring(
    matches: list[dict],
    half_life_days: float = 180.0,
) -> NBAScoringModel:
    """Fit EWMA sur matchs terminés. ignore pré-saison et matchs sans score."""
    finished = [
        m for m in matches
        if m.get("home_score") is not None
        and m.get("away_score") is not None
        and m.get("competition") != "NBA Preseason"
    ]
    if len(finished) < 30:
        return NBAScoringModel(converged=False)

    all_scores = [m["home_score"] for m in finished] + [m["away_score"] for m in finished]
    league_avg = float(np.mean(all_scores))

    ref_date = max(m["match_date"] for m in finished)
    decay = math.log(2.0) / half_life_days

    off_sum: dict[str, float] = {}
    off_w:   dict[str, float] = {}
    def_sum: dict[str, float] = {}
    def_w:   dict[str, float] = {}

    for m in finished:
        age_days = _days_between(ref_date, m["match_date"])
        weight = math.exp(-decay * age_days)
        home = m["home_team_id"]
        away = m["away_team_id"]
        hs = float(m["home_score"])
        as_ = float(m["away_score"])

        off_sum[home] = off_sum.get(home, 0.0) + weight * hs
        off_w[home]   = off_w.get(home, 0.0) + weight
        def_sum[home] = def_sum.get(home, 0.0) + weight * as_
        def_w[home]   = def_w.get(home, 0.0) + weight

        off_sum[away] = off_sum.get(away, 0.0) + weight * as_
        off_w[away]   = off_w.get(away, 0.0) + weight
        def_sum[away] = def_sum.get(away, 0.0) + weight * hs
        def_w[away]   = def_w.get(away, 0.0) + weight

    off_rating = {t: off_sum[t] / off_w[t] for t in off_sum if off_w[t] > 0}
    def_rating = {t: def_sum[t] / def_w[t] for t in def_sum if def_w[t] > 0}

    return NBAScoringModel(
        off_rating=off_rating,
        def_rating=def_rating,
        league_avg=league_avg,
        converged=True,
    )
