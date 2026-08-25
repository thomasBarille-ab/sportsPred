"""Job d'évaluation : calcule les métriques pour les prédictions dont le
résultat est maintenant connu, et les écrit dans prediction_scores.
"""

from __future__ import annotations

import structlog

from ..db import session
from ..scoring.metrics import score_prediction

log = structlog.get_logger()


def run_evaluate(sport: str) -> int:
    """Évalue toutes les prédictions non encore scorées dont le match est terminé."""
    rows = session.fetch_all(
        """
        SELECT p.id         AS prediction_id,
               p.fixture_id,
               p.prob_home_win,
               p.prob_draw,
               p.prob_away_win,
               p.predicted_outcome,
               r.home_score,
               r.away_score
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        JOIN results  r ON r.fixture_id = p.fixture_id
        LEFT JOIN prediction_scores ps ON ps.prediction_id = p.id
        WHERE f.sport = %s AND ps.id IS NULL
        """,
        (sport,),
    )

    n_scored = 0
    for row in rows:
        try:
            scores = score_prediction(
                prob_home=row["prob_home_win"],
                prob_draw=row["prob_draw"],
                prob_away=row["prob_away_win"],
                predicted_outcome=row["predicted_outcome"],
                actual_home_score=row["home_score"],
                actual_away_score=row["away_score"],
                sport=sport,
            )
            session.execute(
                """
                INSERT INTO prediction_scores
                  (prediction_id, fixture_id, brier_score, log_loss, is_correct)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (prediction_id) DO NOTHING
                """,
                (
                    row["prediction_id"],
                    row["fixture_id"],
                    scores["brier_score"],
                    scores["log_loss"],
                    scores["is_correct"],
                ),
            )
            n_scored += 1
        except Exception as exc:
            log.error("evaluate.error", prediction_id=row["prediction_id"], error=str(exc))

    log.info("evaluate.done", sport=sport, n_scored=n_scored)
    return n_scored
