"""Job de réentraînement hebdomadaire avec pattern champion/challenger."""

from __future__ import annotations

from datetime import datetime

import structlog

from ..db import session
from ..training.champion_challenger import maybe_promote, register_model_version
from ..training.trainer import TrainResult, train_model

log = structlog.get_logger()


def run_retrain(sport: str, model_storage_path: str) -> dict:
    log.info("retrain.start", sport=sport)

    # Récupère tous les matchs terminés depuis la DB
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

    if len(all_matches) < 60:
        log.warning("retrain.insufficient_data", sport=sport, n=len(all_matches))
        return {"status": "skipped", "reason": "insufficient_data", "n_matches": len(all_matches)}

    try:
        result: TrainResult = train_model(
            sport=sport,
            all_matches=all_matches,
            model_storage_path=model_storage_path,
        )
    except ValueError as exc:
        log.error("retrain.train_failed", sport=sport, error=str(exc))
        return {"status": "failed", "error": str(exc)}

    # Génère un numéro de version basé sur le timestamp
    version = datetime.utcnow().strftime("v%Y%m%d_%H%M")

    # Récupère le path du modèle sauvegardé depuis le trainer
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
        "retrain.done",
        sport=sport,
        version=version,
        version_id=version_id,
        holdout_brier=round(result.holdout_brier, 5),
        holdout_accuracy=round(result.holdout_accuracy, 3),
        promoted=promoted,
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
