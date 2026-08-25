"""Logique champion / challenger pour la promotion de modèle.

Un nouveau modèle est promu en production si :
  1. Sa Brier score sur le holdout est inférieure (= meilleure) au modèle
     actuellement en production d'au moins IMPROVEMENT_THRESHOLD.
  2. Il y a au moins MIN_HOLDOUT_SAMPLES matchs dans le holdout.

Si aucun modèle n'est encore en production, le premier modèle entraîné
est automatiquement promu.
"""

from __future__ import annotations

import structlog

from ..db import session

log = structlog.get_logger()

IMPROVEMENT_THRESHOLD = 0.002  # amélioration minimum du Brier score pour la promotion
MIN_HOLDOUT_SAMPLES   = 10


def get_production_model(sport: str) -> dict | None:
    return session.fetch_one(
        "SELECT * FROM model_versions WHERE sport = %s AND is_production = TRUE LIMIT 1",
        (sport,),
    )


def register_model_version(
    sport: str,
    version: str,
    model_path: str,
    training_samples: int,
    holdout_samples: int,
    holdout_brier: float,
    holdout_logloss: float,
    holdout_accuracy: float,
    feature_names: list[str],
    hyperparameters: dict,
) -> int:
    """Insère la version en DB et retourne son id."""
    import json
    return session.execute_returning(
        """
        INSERT INTO model_versions
          (sport, version, training_samples, holdout_samples,
           holdout_brier, holdout_logloss, holdout_accuracy,
           is_production, model_path, feature_names, hyperparameters)
        VALUES (%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s,%s)
        RETURNING id
        """,
        (
            sport, version, training_samples, holdout_samples,
            holdout_brier, holdout_logloss, holdout_accuracy,
            model_path,
            json.dumps(feature_names),
            json.dumps(hyperparameters),
        ),
    )


def maybe_promote(sport: str, new_version_id: int, new_brier: float, holdout_samples: int) -> bool:
    """Promeut new_version_id si il bat le champion actuel.

    Retourne True si promu, False sinon.
    """
    if holdout_samples < MIN_HOLDOUT_SAMPLES:
        log.warning(
            "champion_challenger.skip_promotion",
            sport=sport,
            reason="not_enough_holdout_samples",
            holdout_samples=holdout_samples,
        )
        return False

    champion = get_production_model(sport)

    if champion is None:
        _promote(sport, new_version_id)
        log.info("champion_challenger.first_model_promoted", sport=sport, version_id=new_version_id)
        return True

    champion_brier = champion["holdout_brier"]
    delta = champion_brier - new_brier

    log.info(
        "champion_challenger.comparison",
        sport=sport,
        champion_id=champion["id"],
        champion_brier=round(champion_brier, 5),
        challenger_brier=round(new_brier, 5),
        delta=round(delta, 5),
        threshold=IMPROVEMENT_THRESHOLD,
    )

    if delta >= IMPROVEMENT_THRESHOLD:
        _promote(sport, new_version_id)
        log.info(
            "champion_challenger.promoted",
            sport=sport,
            old_champion_id=champion["id"],
            new_champion_id=new_version_id,
        )
        return True

    log.info("champion_challenger.not_promoted", sport=sport, new_version_id=new_version_id)
    return False


def _promote(sport: str, version_id: int) -> None:
    """Marque version_id comme production et démote tous les autres."""
    session.execute(
        "UPDATE model_versions SET is_production = FALSE WHERE sport = %s",
        (sport,),
    )
    session.execute(
        "UPDATE model_versions SET is_production = TRUE WHERE id = %s",
        (version_id,),
    )
