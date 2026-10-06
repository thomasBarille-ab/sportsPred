"""Logique champion / challenger pour la promotion de modèle.

Un nouveau modèle est promu en production si :
  1. Sa Brier score sur le holdout est inférieure (= meilleure) au modèle
     actuellement en production d'au moins IMPROVEMENT_THRESHOLD.
  2. Il y a au moins MIN_HOLDOUT_SAMPLES matchs dans le holdout.

Si aucun modèle n'est encore en production, le premier modèle entraîné
est automatiquement promu.
"""

from __future__ import annotations

import numpy as np
import structlog

from ..db import session

log = structlog.get_logger()

IMPROVEMENT_THRESHOLD = 0.002  # fallback si holdout trop petit pour le bootstrap
MIN_HOLDOUT_SAMPLES   = 50
_BOOTSTRAP_N          = 1000
_BOOTSTRAP_CONFIDENCE = 0.95


def _bootstrap_brier_delta_ci(
    challenger_scores: list[float],
    champion_scores: list[float],
) -> tuple[float, float]:
    """IC bootstrap sur (champion_brier - challenger_brier) par match apparié.

    Borne inférieure > 0 → challenger statistiquement meilleur à 95 %.
    """
    rng = np.random.default_rng(42)
    diffs = np.array(champion_scores) - np.array(challenger_scores)
    n = len(diffs)
    samples = [float(rng.choice(diffs, size=n, replace=True).mean()) for _ in range(_BOOTSTRAP_N)]
    alpha = (1 - _BOOTSTRAP_CONFIDENCE) / 2
    return float(np.quantile(samples, alpha)), float(np.quantile(samples, 1 - alpha))


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
    variant: str = "standard",
) -> int:
    """Insère la version en DB et retourne son id."""
    import json
    return session.execute_returning(
        """
        INSERT INTO model_versions
          (sport, version, training_samples, holdout_samples,
           holdout_brier, holdout_logloss, holdout_accuracy,
           is_production, model_path, feature_names, hyperparameters, variant)
        VALUES (%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s,%s,%s)
        RETURNING id
        """,
        (
            sport, version, training_samples, holdout_samples,
            holdout_brier, holdout_logloss, holdout_accuracy,
            model_path,
            json.dumps(feature_names),
            json.dumps(hyperparameters),
            variant,
        ),
    )


def maybe_promote(
    sport: str,
    new_version_id: int,
    new_brier: float,
    holdout_samples: int,
    champion_brier_override: float | None = None,
    challenger_brier_scores: list[float] | None = None,
    champion_brier_scores: list[float] | None = None,
    variant: str = "standard",
) -> bool:
    """Promeut new_version_id si il bat le champion actuel.

    Utilise un test bootstrap apparié quand les scores par match sont disponibles
    (IC 95 % doit exclure 0). Sinon, fallback sur le seuil fixe IMPROVEMENT_THRESHOLD.

    Seul variant='standard' peut devenir le modèle de production.
    """
    # Le variant no-odds n'est jamais promu en production
    if variant != "standard":
        log.info("champion_challenger.no_odds_not_promoted", sport=sport, variant=variant)
        return False

    champion = get_production_model(sport)

    # Premier modèle : promotion automatique
    if champion is None:
        _promote(sport, new_version_id)
        log.info("champion_challenger.first_model_promoted", sport=sport, version_id=new_version_id)
        return True

    if holdout_samples < MIN_HOLDOUT_SAMPLES:
        log.warning(
            "champion_challenger.skip_promotion",
            sport=sport,
            reason="not_enough_holdout_samples",
            holdout_samples=holdout_samples,
        )
        return False

    if champion_brier_override is not None:
        champion_brier = champion_brier_override
        brier_source = "holdout_apparié"
    else:
        champion_brier = champion["holdout_brier"]
        brier_source = "holdout_db"

    delta = champion_brier - new_brier

    # ── Bootstrap CI si scores par match disponibles ─────────────────────────
    promoted = False
    if challenger_brier_scores and champion_brier_scores and len(challenger_brier_scores) == len(champion_brier_scores):
        ci_low, ci_high = _bootstrap_brier_delta_ci(challenger_brier_scores, champion_brier_scores)
        log.info(
            "champion_challenger.bootstrap_comparison",
            sport=sport,
            champion_id=champion["id"],
            champion_brier=round(champion_brier, 5),
            challenger_brier=round(new_brier, 5),
            delta=round(delta, 5),
            ci_95=f"[{ci_low:.5f}, {ci_high:.5f}]",
            brier_source=brier_source,
            verdict="challenger GAGNE" if ci_low > 0 else "champion conservé",
        )
        if ci_low > 0:
            _promote(sport, new_version_id)
            promoted = True
    else:
        # Fallback : seuil fixe
        log.info(
            "champion_challenger.fixed_threshold_comparison",
            sport=sport,
            champion_id=champion["id"],
            champion_brier=round(champion_brier, 5),
            challenger_brier=round(new_brier, 5),
            delta=round(delta, 5),
            threshold=IMPROVEMENT_THRESHOLD,
            brier_source=brier_source,
        )
        if delta >= IMPROVEMENT_THRESHOLD:
            _promote(sport, new_version_id)
            promoted = True

    if promoted:
        log.info("champion_challenger.promoted", sport=sport,
                 old_champion_id=champion["id"], new_champion_id=new_version_id)
    else:
        log.info("champion_challenger.not_promoted", sport=sport, new_version_id=new_version_id)

    return promoted


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
