"""Job de prédiction : génère et verrouille une prédiction par match futur
qui n'en a pas encore une.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import joblib
import numpy as np
import structlog

from ..db import session
from ..features.builder import build_features_ligue1, build_features_nba
from ..features.elo import build_elo_state, compute_elo_ratings

log = structlog.get_logger()


def _get_all_matches_for_sport(sport: str) -> list[dict]:
    return session.fetch_all(
        """
        SELECT f.home_team_id, f.away_team_id, f.match_date,
               f.home_score, f.away_score
        FROM fixtures f
        WHERE f.sport = %s
        ORDER BY f.match_date
        """,
        (sport,),
    )


def run_predict(sport: str) -> int:
    """Génère les prédictions pour tous les matchs futurs sans prédiction."""
    prod_model = session.fetch_one(
        "SELECT * FROM model_versions WHERE sport = %s AND is_production = TRUE LIMIT 1",
        (sport,),
    )
    if not prod_model:
        log.warning("predict.no_production_model", sport=sport)
        return 0

    artifact = joblib.load(prod_model["model_path"])
    model       = artifact["model"]
    feature_names = artifact["feature_names"]
    dc_model    = artifact.get("dc_model")
    elo_state   = artifact.get("elo")

    if elo_state is None:
        all_matches = _get_all_matches_for_sport(sport)
        ratings = compute_elo_ratings(all_matches, sport)
        elo_state = build_elo_state(sport)
        elo_state.ratings = ratings
    else:
        # Met à jour l'Elo avec les matchs postérieurs à l'entraînement
        # (approximation : on prend simplement les matchs récents)
        all_matches = _get_all_matches_for_sport(sport)

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=7)

    unpredicted = session.fetch_all(
        """
        SELECT f.id, f.home_team_id, f.home_team_name,
               f.away_team_id, f.away_team_name, f.match_date
        FROM fixtures f
        LEFT JOIN predictions p ON p.fixture_id = f.id
        WHERE f.sport = %s
          AND f.match_date BETWEEN %s AND %s
          AND f.status NOT IN ('FINISHED', 'CANCELLED')
          AND p.id IS NULL
        ORDER BY f.match_date
        """,
        (sport, now, horizon),
    )

    n_predicted = 0
    all_matches_list = _get_all_matches_for_sport(sport)

    for fixture in unpredicted:
        try:
            match_date = fixture["match_date"]
            if match_date.tzinfo is None:
                match_date = match_date.replace(tzinfo=timezone.utc)

            if sport == "ligue1":
                vec, _ = build_features_ligue1(
                    fixture["home_team_id"], fixture["away_team_id"],
                    match_date, all_matches_list,
                    elo_state, dc_model,
                )
            else:
                vec, _ = build_features_nba(
                    fixture["home_team_id"], fixture["away_team_id"],
                    match_date, all_matches_list,
                    elo_state,
                )

            X = np.array([vec])
            proba = model.predict_proba(X)[0]

            if sport == "ligue1":
                p_home, p_draw, p_away = float(proba[0]), float(proba[1]), float(proba[2])
            else:
                p_home, p_draw, p_away = float(proba[1]), None, float(proba[0])

            # Outcome prédit = classe la plus probable
            if sport == "ligue1":
                max_idx = int(np.argmax(proba))
                outcome_map = {0: "home", 1: "draw", 2: "away"}
                predicted_outcome = outcome_map[max_idx]
            else:
                predicted_outcome = "home" if p_home >= 0.5 else "away"

            snapshot = dict(zip(feature_names, [round(v, 4) for v in vec]))

            session.execute(
                """
                INSERT INTO predictions
                  (fixture_id, model_version_id, prob_home_win, prob_draw,
                   prob_away_win, predicted_outcome, features_snapshot)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (fixture_id) DO NOTHING
                """,
                (
                    fixture["id"], prod_model["id"],
                    p_home, p_draw, p_away,
                    predicted_outcome,
                    json.dumps(snapshot),
                ),
            )
            n_predicted += 1
            log.info(
                "predict.fixture",
                sport=sport,
                fixture_id=fixture["id"],
                home=fixture["home_team_name"],
                away=fixture["away_team_name"],
                predicted=predicted_outcome,
                p_home=round(p_home, 3),
            )

        except Exception as exc:
            log.error("predict.fixture_error", fixture_id=fixture["id"], error=str(exc))
            continue

    log.info("predict.done", sport=sport, n_predicted=n_predicted)
    return n_predicted
