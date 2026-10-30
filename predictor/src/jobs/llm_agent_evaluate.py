"""Scoring post-match des prédictions de l'agent LLM.

Appelé par le job evaluate existant après que les résultats sont connus.
La confiance (0-100) est convertie en probabilités pour calculer le Brier score :
  - NBA  (2 issues) : p_prédit = conf/100, p_autre = 1 - conf/100
  - L1   (3 issues) : p_prédit = conf/100, les 2 autres = (1 - conf/100) / 2
"""

from __future__ import annotations

import structlog

from ..db import session
from ..scoring.metrics import brier_score, outcome_from_scores

log = structlog.get_logger()

_THREE_CLASS = {"ligue1", "france_nt"}


def _confidence_to_probs(
    predicted_outcome: str,
    confidence: int,
    sport: str,
) -> tuple[float, float | None, float]:
    p = confidence / 100.0
    rest = (1.0 - p) / 2 if sport in _THREE_CLASS else (1.0 - p)

    if predicted_outcome == "home":
        p_home = p
        p_draw = rest if sport in _THREE_CLASS else None
        p_away = rest
    elif predicted_outcome == "away":
        p_home = rest
        p_draw = rest if sport in _THREE_CLASS else None
        p_away = p
    else:  # draw
        p_home = rest
        p_draw = p
        p_away = rest

    return p_home, p_draw, p_away


def run_llm_agent_evaluate(sport: str) -> int:
    rows = session.fetch_all(
        """
        SELECT ap.id AS agent_prediction_id,
               ap.fixture_id,
               ap.predicted_outcome,
               ap.confidence,
               r.home_score,
               r.away_score,
               f.home_team_name,
               f.away_team_name
        FROM llm_agent_predictions ap
        JOIN fixtures f ON f.id = ap.fixture_id
        JOIN results  r ON r.fixture_id = ap.fixture_id
        LEFT JOIN llm_agent_scores las ON las.agent_prediction_id = ap.id
        WHERE f.sport = %s AND las.id IS NULL
        """,
        (sport,),
    )

    if not rows:
        return 0

    log.info("llm_agent_evaluate.start", sport=sport, to_score=len(rows))
    n_scored = 0

    for row in rows:
        try:
            actual = outcome_from_scores(row["home_score"], row["away_score"], sport)
            p_home, p_draw, p_away = _confidence_to_probs(
                row["predicted_outcome"], row["confidence"], sport
            )
            bs = brier_score(p_home, p_draw, p_away, actual, sport)
            is_correct = row["predicted_outcome"] == actual

            session.execute(
                """
                INSERT INTO llm_agent_scores
                  (agent_prediction_id, fixture_id, actual_outcome, is_correct, brier_score)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (fixture_id) DO NOTHING
                """,
                (row["agent_prediction_id"], row["fixture_id"],
                 actual, is_correct, round(bs, 6)),
            )
            n_scored += 1
            log.info(
                "llm_agent_evaluate.scored",
                match=f"{row['home_team_name']} vs {row['away_team_name']}",
                predicted=row["predicted_outcome"],
                actual=actual,
                correct=is_correct,
                brier=round(bs, 4),
            )
        except Exception as exc:
            log.error(
                "llm_agent_evaluate.error",
                fixture_id=row["fixture_id"],
                error=str(exc),
            )

    return n_scored
