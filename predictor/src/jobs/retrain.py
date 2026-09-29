"""Job de réentraînement hebdomadaire avec pattern champion/challenger."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import structlog

from ..db import session
from ..training.champion_challenger import (
    IMPROVEMENT_THRESHOLD,
    MIN_HOLDOUT_SAMPLES,
    get_production_model,
    maybe_promote,
    register_model_version,
)
from ..training.trainer import (
    HYPERPARAMS_FRANCE_NT,
    HYPERPARAMS_LIGUE1,
    HYPERPARAMS_NBA,
    PIPELINE_VERSION,
    TrainResult,
    train_model,
)

log = structlog.get_logger()


def _score_champion_on_holdout(
    champion: dict,
    X_test: np.ndarray,
    y_test: np.ndarray,
    sport: str,
) -> float | None:
    """Score le champion sur le X_test du challenger.

    Retourne None si le champion est legacy (pas de pipeline_version >= 2)
    ou si les feature_names diffèrent.
    """
    import json
    import joblib
    from ..training.trainer import _compute_metrics

    try:
        artifact = joblib.load(champion["model_path"])
    except Exception as exc:
        log.warning("retrain.champion_load_failed", error=str(exc))
        return None

    # Vérifie la version du pipeline
    if artifact.get("pipeline_version", 0) < 4:
        log.info(
            "retrain.champion_legacy",
            sport=sport,
            reason="pipeline_version < 4 — considéré comme legacy, promotion automatique du challenger",
        )
        return None

    # Vérifie que les feature sets sont identiques
    champ_features = artifact.get("feature_names", [])
    champ_fn_set = set(champ_features)
    from ..features.builder import FRANCE_NT_FEATURES, LIGUE1_FEATURES, NBA_FEATURES
    if sport == "france_nt":
        exp_features = FRANCE_NT_FEATURES
    elif sport == "ligue1":
        exp_features = LIGUE1_FEATURES
    else:
        exp_features = NBA_FEATURES
    if champ_fn_set != set(exp_features):
        log.info(
            "retrain.feature_mismatch",
            sport=sport,
            reason="feature_names différents — champion considéré comme legacy",
        )
        return None

    proba = artifact["model"].predict_proba(X_test)
    brier, ll, _ = _compute_metrics(proba, y_test, sport)
    log.info(
        "retrain.champion_scored_on_challenger_holdout",
        sport=sport,
        champion_brier=round(brier, 5),
        champion_logloss=round(ll, 5),
    )
    return brier


def run_retrain(sport: str, model_storage_path: str) -> dict:
    log.info("retrain.start", sport=sport)

    all_matches = session.fetch_all(
        """
        SELECT f.id, f.home_team_id, f.away_team_id, f.match_date,
               f.home_score, f.away_score, f.season,
               f.competition, f.home_xg, f.away_xg
        FROM fixtures f
        WHERE f.sport = %s AND f.home_score IS NOT NULL
        ORDER BY f.match_date
        """,
        (sport,),
    )

    fixture_ids = [m["id"] for m in all_matches]
    odds_by_fixture: dict[int, tuple] = {}
    if fixture_ids:
        odds_rows = session.fetch_all(
            """
            SELECT DISTINCT ON (fixture_id) fixture_id, odds_home, odds_draw, odds_away
            FROM match_odds
            WHERE fixture_id = ANY(%s)
            ORDER BY fixture_id,
              CASE WHEN bookmaker = 'pinnacle' THEN 0 ELSE 1 END,
              fetched_at DESC
            """,
            (fixture_ids,),
        )
        for r in odds_rows:
            odds_by_fixture[r["fixture_id"]] = (r["odds_home"], r["odds_draw"], r["odds_away"])

    min_required = 30 if sport == "france_nt" else 60
    log.info(
        "retrain.données",
        sport=sport,
        matchs_historiques=len(all_matches),
        seuil_minimum=min_required,
    )

    if len(all_matches) < min_required:
        log.warning(
            "retrain.données_insuffisantes",
            sport=sport,
            n=len(all_matches),
            manquants=min_required - len(all_matches),
            action="skip — entraînement annulé",
        )
        return {"status": "skipped", "reason": "insufficient_data", "n_matches": len(all_matches)}

    # Pour France NT : charge les données de disponibilité depuis les lineups
    availability_by_fixture: dict[int, tuple[int, float]] = {}
    if sport == "france_nt":
        lineup_rows = session.fetch_all(
            """
            SELECT fixture_id,
                   COUNT(*) FILTER (WHERE is_starter)               AS n_starters_total,
                   11                                                AS xi_size
            FROM france_nt_lineups
            GROUP BY fixture_id
            """,
        )
        for row in lineup_rows:
            fid = row["fixture_id"]
            n_total = int(row["n_starters_total"] or 0)
            ratio = n_total / 11.0 if n_total > 0 else 1.0
            availability_by_fixture[fid] = (0, ratio)

    # Charge le contexte agent pour tous les sports supportés
    context_by_fixture: dict[int, dict] = {}
    if sport in ("ligue1", "france_nt") and fixture_ids:
        ctx_rows = session.fetch_all(
            """
            SELECT fixture_id,
                   home_injuries_count, away_injuries_count,
                   home_rotation_signal, away_rotation_signal,
                   weather_rain_mm, weather_wind_kmh
            FROM match_context
            WHERE fixture_id = ANY(%s)
            """,
            (fixture_ids,),
        )
        for r in ctx_rows:
            context_by_fixture[r["fixture_id"]] = {
                "home_injuries_count":  r["home_injuries_count"],
                "away_injuries_count":  r["away_injuries_count"],
                "home_rotation_signal": r["home_rotation_signal"],
                "away_rotation_signal": r["away_rotation_signal"],
                "weather_rain_mm":      r["weather_rain_mm"] or 0.0,
                "weather_wind_kmh":     r["weather_wind_kmh"] or 0.0,
            }
        log.info("retrain.context_loaded", sport=sport, n_with_context=len(context_by_fixture))

    log.info("retrain.entraînement_en_cours", sport=sport,
             note="XGBoost — 400 estimateurs, profondeur max 4, lr 0.05")

    try:
        result: TrainResult = train_model(
            sport=sport,
            all_matches=all_matches,
            model_storage_path=model_storage_path,
            odds_by_fixture=odds_by_fixture,
            availability_by_fixture=availability_by_fixture if sport == "france_nt" else None,
            context_by_fixture=context_by_fixture if context_by_fixture else None,
        )
    except ValueError as exc:
        log.error("retrain.échec_entraînement", sport=sport, erreur=str(exc))
        return {"status": "failed", "error": str(exc)}

    log.info(
        "retrain.résultats_challenger",
        sport=sport,
        train_samples=result.training_samples,
        holdout_samples=result.holdout_samples,
        holdout_brier=round(result.holdout_brier, 5),
        holdout_logloss=round(result.holdout_logloss, 5),
        holdout_accuracy=f"{result.holdout_accuracy:.1%}",
    )

    # ── Comparaison champion/challenger sur le MÊME holdout ──────────────────
    champion = get_production_model(sport)
    champion_brier_on_same_holdout: float | None = None

    if champion:
        champion_brier_on_same_holdout = _score_champion_on_holdout(
            champion, result.X_test, result.y_test, sport
        )

        if champion_brier_on_same_holdout is not None:
            delta = champion_brier_on_same_holdout - result.holdout_brier
            log.info(
                "retrain.comparaison_champion_challenger",
                sport=sport,
                champion_brier=round(champion_brier_on_same_holdout, 5),
                challenger_brier=round(result.holdout_brier, 5),
                delta=f"{delta:+.5f}",
                seuil=IMPROVEMENT_THRESHOLD,
                verdict="challenger GAGNE" if delta >= IMPROVEMENT_THRESHOLD else f"champion conservé (delta {delta:.5f} < {IMPROVEMENT_THRESHOLD})",
            )
        else:
            log.info("retrain.champion_legacy", sport=sport,
                     note="Champion non comparable — promotion automatique du challenger")
    else:
        log.info("retrain.premier_modèle", sport=sport,
                 note="Aucun champion existant — promotion automatique")

    version = datetime.now(timezone.utc).strftime("v%Y%m%d_%H%M%S")
    model_path = result.model_path

    if sport == "france_nt":
        _hp = HYPERPARAMS_FRANCE_NT
    elif sport == "ligue1":
        _hp = HYPERPARAMS_LIGUE1
    else:
        _hp = HYPERPARAMS_NBA
    hyperparams = _hp.copy()
    hyperparams["pipeline_version"] = PIPELINE_VERSION

    version_id = register_model_version(
        sport=sport,
        version=version,
        model_path=model_path,
        training_samples=result.training_samples,
        holdout_samples=result.holdout_samples,
        holdout_brier=result.holdout_brier,
        holdout_logloss=result.holdout_logloss,
        holdout_accuracy=result.holdout_accuracy,
        feature_names=result.feature_names,
        hyperparameters=hyperparams,
    )

    # Sauvegarde des importances de features
    try:
        import psycopg2.extras
        importances = result.model.feature_importances_
        rows = [
            (version_id, sport, name, float(imp))
            for name, imp in zip(result.feature_names, importances)
        ]
        with session.get_conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO feature_importances (model_version_id, sport, feature_name, importance) VALUES %s",
                    rows,
                )
        log.info("retrain.feature_importances_saved", sport=sport, n=len(rows))
    except Exception as exc:
        log.warning("retrain.feature_importances_failed", sport=sport, error=str(exc))

    # Si le champion est legacy → challenger promu automatiquement
    effective_champion_brier = (
        champion_brier_on_same_holdout
        if champion_brier_on_same_holdout is not None and champion
        else None
    )

    promoted = maybe_promote(
        sport,
        version_id,
        result.holdout_brier,
        result.holdout_samples,
        champion_brier_override=effective_champion_brier,
    )

    log.info(
        "retrain.terminé",
        sport=sport,
        version=version,
        promu_en_production=promoted,
        action="Le nouveau modèle prend le relais" if promoted else "L'ancien modèle reste en production",
    )

    return {
        "status": "success",
        "version": version,
        "version_id": version_id,
        "holdout_brier": result.holdout_brier,
        "holdout_logloss": result.holdout_logloss,
        "holdout_accuracy": result.holdout_accuracy,
        "promoted": promoted,
    }
