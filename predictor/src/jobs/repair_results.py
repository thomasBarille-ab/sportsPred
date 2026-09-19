"""Réparation des résultats incohérents entre fixtures et results.

Idempotent : peut être relancé sans effet de bord.
Usage : python -m src.jobs.repair_results

Pour chaque fixture FINISHED dont les scores en base diffèrent de ceux dans
la table results, met à jour results (scores + actual_outcome) et supprime
les prediction_scores correspondants pour qu'evaluate les recalcule.
"""

from __future__ import annotations

import structlog

from ..db import session
from ..scoring.metrics import outcome_from_scores

log = structlog.get_logger()


def run_repair_results() -> dict:
    rows = session.fetch_all(
        """
        SELECT f.id         AS fixture_id,
               f.sport,
               f.home_score AS fix_home,
               f.away_score AS fix_away,
               r.id         AS result_id,
               r.home_score AS res_home,
               r.away_score AS res_away
        FROM fixtures f
        JOIN results r ON r.fixture_id = f.id
        WHERE f.status = 'FINISHED'
          AND f.home_score IS NOT NULL
          AND (r.home_score IS DISTINCT FROM f.home_score
               OR r.away_score IS DISTINCT FROM f.away_score)
        """,
    )

    n_fixed = 0
    for row in rows:
        sport       = row["sport"]
        fix_home    = row["fix_home"]
        fix_away    = row["fix_away"]
        outcome     = outcome_from_scores(fix_home, fix_away, sport)
        fixture_id  = row["fixture_id"]

        session.execute(
            """
            UPDATE results
            SET home_score = %s, away_score = %s, actual_outcome = %s
            WHERE id = %s
            """,
            (fix_home, fix_away, outcome, row["result_id"]),
        )
        # Supprime les scores calculés sur l'ancien résultat
        session.execute(
            """
            DELETE FROM prediction_scores
            WHERE prediction_id IN (
                SELECT id FROM predictions WHERE fixture_id = %s
            )
            """,
            (fixture_id,),
        )
        n_fixed += 1

    log.info("repair_results.done", lignes_corrigées=n_fixed)
    return {"fixed": n_fixed}


if __name__ == "__main__":
    import sys
    from ..config import load_settings
    from ..db import session as db

    cfg = load_settings()
    db.init_pool(cfg.postgres_url)
    result = run_repair_results()
    sys.exit(0)
