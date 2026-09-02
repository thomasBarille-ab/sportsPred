"""Job d'évaluation : calcule les métriques pour les prédictions dont le
résultat est maintenant connu, et les écrit dans prediction_scores.
"""

from __future__ import annotations

import structlog

from ..db import session
from ..scoring.metrics import outcome_from_scores, score_prediction

log = structlog.get_logger()


def run_evaluate(sport: str) -> int:
    """Évalue toutes les prédictions non encore scorées dont le match est terminé."""
    rows = session.fetch_all(
        """
        SELECT p.id             AS prediction_id,
               p.fixture_id,
               p.prob_home_win,
               p.prob_draw,
               p.prob_away_win,
               p.predicted_outcome,
               r.home_score,
               r.away_score,
               f.home_team_name,
               f.away_team_name
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        JOIN results  r ON r.fixture_id = p.fixture_id
        LEFT JOIN prediction_scores ps ON ps.prediction_id = p.id
        WHERE f.sport = %s AND ps.id IS NULL
        """,
        (sport,),
    )

    log.info(
        "evaluate.start",
        sport=sport,
        prédictions_à_scorer=len(rows),
    )

    if not rows:
        log.info("evaluate.rien_à_faire", sport=sport,
                 reason="Aucune prédiction en attente de résultat")
        return 0

    n_scored = n_correct = n_wrong = 0

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

            actual_outcome = outcome_from_scores(row["home_score"], row["away_score"], sport)
            correct = scores["is_correct"]
            verdict = "✓ CORRECT" if correct else "✗ RATÉ"
            n_scored += 1
            if correct: n_correct += 1
            else:       n_wrong  += 1

            log.info(
                "evaluate.résultat",
                match=f"{row['home_team_name']} vs {row['away_team_name']}",
                score=f"{row['home_score']}-{row['away_score']}",
                prédit=row["predicted_outcome"],
                réel=actual_outcome,
                verdict=verdict,
                brier=round(scores["brier_score"], 4),
            )

        except Exception as exc:
            log.error("evaluate.erreur",
                      prediction_id=row["prediction_id"],
                      match=f"{row.get('home_team_name','?')} vs {row.get('away_team_name','?')}",
                      erreur=str(exc))

    accuracy = n_correct / n_scored if n_scored else 0
    log.info(
        "evaluate.bilan",
        sport=sport,
        scorés=n_scored,
        corrects=n_correct,
        ratés=n_wrong,
        accuracy_batch=f"{accuracy:.0%}",
    )
    return n_scored
