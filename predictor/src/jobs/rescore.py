"""Recalcule brier_score et log_loss de toutes les prediction_scores existantes.

Idempotent : peut être relancé. Utile après un changement de définition du Brier score
(par exemple passage de la version /2 à la version somme des carrés).

Usage : python -m src.jobs.rescore
"""

from __future__ import annotations

import structlog

from ..db import session
from ..scoring.metrics import brier_score, log_loss_single, outcome_from_scores

log = structlog.get_logger()


def run_rescore() -> dict:
    rows = session.fetch_all(
        """
        SELECT ps.id          AS score_id,
               ps.prediction_id,
               p.prob_home_win,
               p.prob_draw,
               p.prob_away_win,
               p.predicted_outcome,
               r.home_score,
               r.away_score,
               f.sport
        FROM prediction_scores ps
        JOIN predictions p ON p.id = ps.prediction_id
        JOIN fixtures f    ON f.id = p.fixture_id
        JOIN results r     ON r.fixture_id = f.id
        """,
    )

    n_updated = 0
    for row in rows:
        actual = outcome_from_scores(row["home_score"], row["away_score"], row["sport"])
        bs = brier_score(
            row["prob_home_win"], row["prob_draw"], row["prob_away_win"],
            actual, row["sport"],
        )
        ll = log_loss_single(
            row["prob_home_win"], row["prob_draw"], row["prob_away_win"],
            actual, row["sport"],
        )
        session.execute(
            "UPDATE prediction_scores SET brier_score = %s, log_loss = %s WHERE id = %s",
            (bs, ll, row["score_id"]),
        )
        n_updated += 1

    log.info("rescore.done", lignes_mises_à_jour=n_updated)
    return {"updated": n_updated}


if __name__ == "__main__":
    import sys
    from ..config import load_settings
    from ..db import session as db

    cfg = load_settings()
    db.init_pool(cfg.postgres_url)
    result = run_rescore()
    sys.exit(0)
