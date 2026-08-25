"""Système de rating Elo pour Ligue 1 et NBA.

Paramètres par sport :
  Ligue 1 : K=32, home_advantage=+65 Elo points
  NBA      : K=20, home_advantage=+100 (la NBA a un fort home-court advantage)

Rating initial : 1500 pour toutes les équipes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class EloState:
    ratings: dict[str, float] = field(default_factory=dict)
    k_factor: float = 32.0
    home_advantage: float = 65.0
    initial_rating: float = 1500.0

    def get(self, team_id: str) -> float:
        return self.ratings.get(team_id, self.initial_rating)

    def expected_home(self, home_id: str, away_id: str) -> float:
        """Probabilité de victoire de l'équipe à domicile."""
        home_r = self.get(home_id) + self.home_advantage
        away_r = self.get(away_id)
        return 1.0 / (1.0 + math.pow(10, (away_r - home_r) / 400.0))

    def update(
        self,
        home_id: str,
        away_id: str,
        home_score: int,
        away_score: int,
        sport: str = "ligue1",
    ) -> None:
        """Mise à jour des ratings après un match terminé."""
        e_home = self.expected_home(home_id, away_id)
        e_away = 1.0 - e_home

        if home_score > away_score:
            s_home, s_away = 1.0, 0.0
        elif home_score == away_score:
            s_home, s_away = 0.5, 0.5
        else:
            s_home, s_away = 0.0, 1.0

        r_home = self.get(home_id)
        r_away = self.get(away_id)
        self.ratings[home_id] = r_home + self.k_factor * (s_home - e_home)
        self.ratings[away_id] = r_away + self.k_factor * (s_away - e_away)


def build_elo_state(sport: str) -> EloState:
    if sport == "ligue1":
        return EloState(k_factor=32.0, home_advantage=65.0)
    elif sport == "nba":
        return EloState(k_factor=20.0, home_advantage=100.0)
    raise ValueError(f"Unknown sport: {sport!r}")


def compute_elo_ratings(
    matches: list[dict],
    sport: str,
) -> dict[str, float]:
    """Calcule les ratings Elo finaux à partir d'une liste de matchs triés chronologiquement.

    Chaque match est un dict avec :
      home_team_id, away_team_id, home_score, away_score, match_date
    Seuls les matchs FINISHED (home_score not None) sont pris en compte.

    Retourne un dict {team_id: elo_rating}.
    """
    state = build_elo_state(sport)
    for m in sorted(matches, key=lambda x: x["match_date"]):
        if m.get("home_score") is None or m.get("away_score") is None:
            continue
        state.update(
            m["home_team_id"],
            m["away_team_id"],
            m["home_score"],
            m["away_score"],
            sport,
        )
    return dict(state.ratings)
