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

_DEFAULT_HORIZON_HOURS = 36


def _get_all_matches_for_sport(sport: str) -> list[dict]:
    return session.fetch_all(
        """
        SELECT f.home_team_id, f.away_team_id, f.match_date,
               f.home_score, f.away_score, f.status
        FROM fixtures f
        WHERE f.sport = %s
        ORDER BY f.match_date
        """,
        (sport,),
    )


def run_predict(sport: str, horizon_hours: int = _DEFAULT_HORIZON_HOURS) -> int:
    """Génère les prédictions pour tous les matchs futurs sans prédiction."""
    prod_model = session.fetch_one(
        "SELECT * FROM model_versions WHERE sport = %s AND is_production = TRUE LIMIT 1",
        (sport,),
    )
    if not prod_model:
        log.warning("predict.no_production_model", sport=sport,
                    reason="Aucun modèle entraîné en production — lancer d'abord le job retrain")
        return 0

    log.info(
        "predict.model_loaded",
        sport=sport,
        version=prod_model.get("version", "?"),
        trained_at=str(prod_model.get("trained_at", "?"))[:10],
        holdout_brier=round(prod_model.get("holdout_brier") or 0, 4),
        holdout_accuracy=round(prod_model.get("holdout_accuracy") or 0, 3),
    )

    artifact = joblib.load(prod_model["model_path"])
    model         = artifact["model"]
    feature_names = artifact["feature_names"]
    dc_model      = artifact.get("dc_model")

    # Charge tous les matchs UNE seule fois
    all_matches = _get_all_matches_for_sport(sport)

    # Recalcule l'Elo depuis TOUS les matchs terminés (ne réutilise plus artifact["elo"])
    finished_matches = [m for m in all_matches if m.get("home_score") is not None]
    ratings = compute_elo_ratings(finished_matches, sport)
    elo_state = build_elo_state(sport)
    elo_state.ratings = ratings

    log.info("predict.elo_recalculated", sport=sport, n_matches=len(finished_matches))

    # Calendrier complet (hors CANCELLED/POSTPONED) pour le calcul du repos
    schedule = [m for m in all_matches if m.get("status") not in ("CANCELLED", "POSTPONED")]

    now     = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_hours)

    unpredicted = session.fetch_all(
        """
        SELECT f.id, f.home_team_id, f.home_team_name,
               f.away_team_id, f.away_team_name, f.match_date
        FROM fixtures f
        LEFT JOIN predictions p ON p.fixture_id = f.id
        WHERE f.sport = %s
          AND f.match_date BETWEEN %s AND %s
          AND f.status IN ('SCHEDULED', 'TIMED')
          AND p.id IS NULL
        ORDER BY f.match_date
        """,
        (sport, now, horizon),
    )

    log.info(
        "predict.fixtures_queued",
        sport=sport,
        count=len(unpredicted),
        horizon_hours=horizon_hours,
    )

    if not unpredicted:
        log.info("predict.nothing_to_do", sport=sport,
                 reason="Tous les matchs à venir ont déjà une prédiction verrouillée")
        return 0

    n_predicted = 0
    # Utilise uniquement les matchs terminés comme historique pour les features
    finished_list = [m for m in all_matches if m.get("home_score") is not None]

    for fixture in unpredicted:
        try:
            match_date = fixture["match_date"]
            if match_date.tzinfo is None:
                match_date = match_date.replace(tzinfo=timezone.utc)

            if sport == "ligue1":
                vec, _ = build_features_ligue1(
                    fixture["home_team_id"], fixture["away_team_id"],
                    match_date, finished_list,
                    elo_state, dc_model,
                    schedule=schedule,
                )
            else:
                vec, _ = build_features_nba(
                    fixture["home_team_id"], fixture["away_team_id"],
                    match_date, finished_list,
                    elo_state,
                    schedule=schedule,
                )

            X     = np.array([vec])
            proba = model.predict_proba(X)[0]

            if sport == "ligue1":
                p_home, p_draw, p_away = float(proba[0]), float(proba[1]), float(proba[2])
            else:
                p_home, p_draw, p_away = float(proba[1]), None, float(proba[0])

            if sport == "ligue1":
                max_idx = int(np.argmax(proba))
                outcome_map = {0: "home", 1: "draw", 2: "away"}
                predicted_outcome = outcome_map[max_idx]
            else:
                predicted_outcome = "home" if p_home >= 0.5 else "away"

            snapshot = dict(zip(feature_names, [round(v, 4) for v in vec]))
            confidence = float(max(proba))

            # ── Log de raisonnement ────────────────────────────────────────────
            if sport == "ligue1":
                elo_diff = int(round(snapshot.get("elo_diff", 0)))
                elo_sign = f"+{elo_diff}" if elo_diff >= 0 else str(elo_diff)
                dc_str = (
                    f"{snapshot.get('dc_p_home', 0):.0%} / "
                    f"{snapshot.get('dc_p_draw', 0):.0%} / "
                    f"{snapshot.get('dc_p_away', 0):.0%}"
                )
                home_form = int(snapshot.get("home_form_pts_last5", 0))
                away_form = int(snapshot.get("away_form_pts_last5", 0))
                form_edge = fixture["home_team_name"] if home_form > away_form else (
                    fixture["away_team_name"] if away_form > home_form else "égaux"
                )
                log.info(
                    "predict.raisonnement",
                    match=f"{fixture['home_team_name']} vs {fixture['away_team_name']}",
                    elo_diff=elo_sign,
                    dc_home_draw_away=dc_str,
                    home_form_5j=f"{home_form}pts",
                    away_form_5j=f"{away_form}pts",
                    forme_avantage=form_edge,
                    decision=predicted_outcome,
                    confiance=f"{confidence:.0%}",
                    p_home=f"{p_home:.1%}",
                    p_draw=f"{p_draw:.1%}" if p_draw is not None else None,
                    p_away=f"{p_away:.1%}",
                )
            else:
                elo_diff = int(round(snapshot.get("elo_diff", 0)))
                elo_sign = f"+{elo_diff}" if elo_diff >= 0 else str(elo_diff)
                h_wr = snapshot.get("home_win_rate_last10", 0)
                a_wr = snapshot.get("away_win_rate_last10", 0)
                h_b2b = bool(snapshot.get("home_b2b", 0))
                a_b2b = bool(snapshot.get("away_b2b", 0))
                fatigue = []
                if h_b2b: fatigue.append(f"{fixture['home_team_name']} B2B")
                if a_b2b: fatigue.append(f"{fixture['away_team_name']} B2B")
                log.info(
                    "predict.raisonnement",
                    match=f"{fixture['home_team_name']} vs {fixture['away_team_name']}",
                    elo_diff=elo_sign,
                    home_winrate_10j=f"{h_wr:.0%}",
                    away_winrate_10j=f"{a_wr:.0%}",
                    fatigue=", ".join(fatigue) if fatigue else "aucune",
                    decision=predicted_outcome,
                    confiance=f"{confidence:.0%}",
                    p_home=f"{p_home:.1%}",
                    p_away=f"{p_away:.1%}",
                )

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

        except Exception as exc:
            log.error("predict.erreur_fixture",
                      fixture_id=fixture["id"],
                      match=f"{fixture.get('home_team_name','?')} vs {fixture.get('away_team_name','?')}",
                      erreur=str(exc))
            continue

    log.info(
        "predict.terminé",
        sport=sport,
        prédictions_générées=n_predicted,
        déjà_existantes=len(unpredicted) - n_predicted,
    )
    return n_predicted
