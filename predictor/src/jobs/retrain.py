"""Job de réentraînement hebdomadaire avec pattern champion/challenger."""

from __future__ import annotations

from datetime import datetime

import structlog

from ..db import session
from ..training.champion_challenger import (
    IMPROVEMENT_THRESHOLD,
    get_production_model,
    maybe_promote,
    register_model_version,
)
from ..training.trainer import TrainResult, train_model

log = structlog.get_logger()


def run_retrain(sport: str, model_storage_path: str) -> dict:
    log.info("retrain.start", sport=sport)

    all_matches = session.fetch_all(
        """
        SELECT f.home_team_id, f.away_team_id, f.match_date,
               f.home_score, f.away_score, f.season
        FROM fixtures f
        WHERE f.sport = %s AND f.home_score IS NOT NULL
        ORDER BY f.match_date
        """,
        (sport,),
    )

    log.info(
        "retrain.données",
        sport=sport,
        matchs_historiques=len(all_matches),
        seuil_minimum=60,
    )

    if len(all_matches) < 60:
        log.warning(
            "retrain.données_insuffisantes",
            sport=sport,
            n=len(all_matches),
            manquants=60 - len(all_matches),
            action="skip — entraînement annulé",
        )
        return {"status": "skipped", "reason": "insufficient_data", "n_matches": len(all_matches)}

    log.info("retrain.entraînement_en_cours", sport=sport,
             note="XGBoost — 400 estimateurs, profondeur max 4, lr 0.05")

    try:
        result: TrainResult = train_model(
            sport=sport,
            all_matches=all_matches,
            model_storage_path=model_storage_path,
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

    # Comparaison avec le champion actuel
    champion = get_production_model(sport)
    if champion:
        delta = champion["holdout_brier"] - result.holdout_brier
        log.info(
            "retrain.comparaison_champion_challenger",
            sport=sport,
            champion_brier=round(champion["holdout_brier"], 5),
            challenger_brier=round(result.holdout_brier, 5),
            delta=f"{delta:+.5f}",
            seuil=IMPROVEMENT_THRESHOLD,
            verdict="challenger GAGNE" if delta >= IMPROVEMENT_THRESHOLD else f"champion conservé (delta {delta:.5f} < {IMPROVEMENT_THRESHOLD})",
        )
    else:
        log.info("retrain.premier_modèle", sport=sport,
                 note="Aucun champion existant — promotion automatique")

    version = datetime.utcnow().strftime("v%Y%m%d_%H%M")

    import glob
    import os
    pattern = os.path.join(model_storage_path, sport, "model_*.joblib")
    files = sorted(glob.glob(pattern))
    model_path = files[-1] if files else ""

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
        hyperparameters={"n_estimators": 400, "max_depth": 4, "learning_rate": 0.05},
    )

    promoted = maybe_promote(sport, version_id, result.holdout_brier, result.holdout_samples)

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
