"""Entraînement XGBoost avec holdout chronologique et pondération temporelle.

Ligue 1 : 3 classes (0=home win, 1=draw, 2=away win) — multi:softprob
NBA      : 2 classes (0=away win, 1=home win) — binary:logistic

Holdout : les 15% les plus récents sont utilisés pour l'évaluation uniquement.
Temporal weighting : les matchs récents sont pondérés plus fortement
  (demi-vie de 365 jours).

Hyperparamètres partagés (source unique) :
  HYPERPARAMS_LIGUE1 / HYPERPARAMS_NBA
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import joblib
import numpy as np
import xgboost as xgb

from ..features.builder import (
    LIGUE1_FEATURES,
    NBA_FEATURES,
    build_features_ligue1,
    build_features_nba,
)
from ..features.dixon_coles import DCModel, dc_model_to_x0, fit_dixon_coles
from ..features.elo import EloState, build_elo_state

# ── Source unique des hyperparamètres ────────────────────────────────────────

HYPERPARAMS_LIGUE1: dict = {
    "n_estimators": 400,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
}

HYPERPARAMS_NBA: dict = {
    "n_estimators": 400,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
}

PIPELINE_VERSION = 2


@dataclass
class TrainResult:
    model: xgb.XGBClassifier
    feature_names: list[str]
    holdout_brier: float
    holdout_logloss: float
    holdout_accuracy: float
    training_samples: int
    holdout_samples: int
    sport: str
    model_path: str = ""
    X_test: np.ndarray = field(default_factory=lambda: np.array([]))
    y_test: np.ndarray = field(default_factory=lambda: np.array([]))


def _outcome_label(home_score: int, away_score: int, sport: str) -> int:
    if sport == "ligue1":
        if home_score > away_score: return 0
        if home_score == away_score: return 1
        return 2
    else:  # nba
        return 1 if home_score > away_score else 0


def _sample_weights(dates: list[datetime], half_life: float = 365.0) -> np.ndarray:
    ref = max(dates)
    lam = math.log(2) / half_life
    ages = np.array([(ref - d).days for d in dates], dtype=float)
    return np.exp(-lam * ages)


def _build_model(sport: str) -> xgb.XGBClassifier:
    if sport == "ligue1":
        return xgb.XGBClassifier(
            objective="multi:softprob",
            num_class=3,
            eval_metric="mlogloss",
            random_state=42,
            n_jobs=-1,
            **HYPERPARAMS_LIGUE1,
        )
    return xgb.XGBClassifier(
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=42,
        n_jobs=-1,
        **HYPERPARAMS_NBA,
    )


def _brier_multiclass(proba: np.ndarray, y: np.ndarray, n_classes: int) -> float:
    """Brier score multi-classe : moyenne de la somme des carrés sur toutes les classes."""
    total = 0.0
    for i, yi in enumerate(y):
        for k in range(n_classes):
            p = float(proba[i, k])
            o = 1.0 if yi == k else 0.0
            total += (p - o) ** 2
    return total / len(y)


def _brier_binary(proba_pos: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((proba_pos - y.astype(float)) ** 2))


def _compute_metrics(
    proba: np.ndarray,
    y: np.ndarray,
    sport: str,
) -> tuple[float, float, float]:
    """Retourne (brier, logloss, accuracy)."""
    from sklearn.metrics import log_loss

    if sport == "ligue1":
        brier = _brier_multiclass(proba, y, n_classes=3)
        ll    = float(log_loss(y, proba, labels=[0, 1, 2]))
        acc   = float(np.mean(np.argmax(proba, axis=1) == y))
    else:
        brier = _brier_binary(proba[:, 1], y)
        ll    = float(log_loss(y, proba))
        acc   = float(np.mean((proba[:, 1] >= 0.5).astype(int) == y))
    return brier, ll, acc


# ─────────────────────────────────────────────────────────────────────────────
# Construction des lignes avec walk-forward Elo + DC
# ─────────────────────────────────────────────────────────────────────────────

_DC_REFIT_INTERVAL_DAYS = 30


def _build_rows_walkforward(
    matches: list[dict],
    sport: str,
    elo_state: EloState,
    dc_state: list,  # [DCModel | None] — conteneur mutable pour le DC courant
    dc_last_refit: list,  # [datetime | None]
    dc_x0: list,  # [np.ndarray | None]
    all_past_fn,  # callable(i) -> list[dict] des matchs passés
) -> tuple[list[list[float]], list[int], list[datetime]]:
    """Construit les features en walk-forward (Elo et DC mis à jour après chaque match)."""
    X, y, dates = [], [], []
    for i, m in enumerate(matches):
        # DC refit tous les ~30 jours (Ligue 1 uniquement)
        if sport == "ligue1":
            if (
                dc_last_refit[0] is None
                or (m["match_date"] - dc_last_refit[0]).days >= _DC_REFIT_INTERVAL_DAYS
            ):
                past_for_dc = all_past_fn(i)
                finished_dc = [x for x in past_for_dc if x.get("home_score") is not None]
                if len(finished_dc) >= 30:
                    dc_state[0] = fit_dixon_coles(
                        past_for_dc,
                        x0=dc_x0[0],
                    )
                    # Warm start pour le prochain refit
                    teams = sorted({x["home_team_id"] for x in finished_dc} | {x["away_team_id"] for x in finished_dc})
                    dc_x0[0] = dc_model_to_x0(dc_state[0], teams)
                dc_last_refit[0] = m["match_date"]

        past = all_past_fn(i)
        try:
            if sport == "ligue1":
                dc = dc_state[0] or DCModel(teams=[], attack={}, defense={}, home_advantage=1.3, rho=-0.1, converged=False)
                vec, _ = build_features_ligue1(
                    m["home_team_id"], m["away_team_id"],
                    m["match_date"], past,
                    elo_state, dc,
                )
            else:
                vec, _ = build_features_nba(
                    m["home_team_id"], m["away_team_id"],
                    m["match_date"], past,
                    elo_state,
                )
        except Exception:
            # Met quand même à jour l'Elo avant de passer à la suite
            elo_state.update(m["home_team_id"], m["away_team_id"], m["home_score"], m["away_score"], sport)
            continue

        X.append(vec)
        y.append(_outcome_label(m["home_score"], m["away_score"], sport))
        dates.append(m["match_date"])

        # Mise à jour APRÈS avoir construit la ligne
        elo_state.update(m["home_team_id"], m["away_team_id"], m["home_score"], m["away_score"], sport)

    return X, y, dates


# ─────────────────────────────────────────────────────────────────────────────
# Entraînement principal
# ─────────────────────────────────────────────────────────────────────────────

def train_model(
    sport: Literal["ligue1", "nba"],
    all_matches: list[dict],
    model_storage_path: str,
    holdout_fraction: float = 0.15,
) -> TrainResult:
    """Entraîne un nouveau modèle et retourne les métriques sur le holdout.

    Retourne TrainResult avec model_path rempli (le chemin vers l'artefact sauvegardé).
    Les métriques holdout sont calculées sur le modèle de validation (85 % des données),
    puis un modèle final est entraîné sur 100 % des données et sauvegardé.
    """
    finished = sorted(
        [m for m in all_matches if m.get("home_score") is not None],
        key=lambda x: x["match_date"],
    )
    if len(finished) < 60:
        raise ValueError(f"Not enough finished matches for {sport}: {len(finished)} < 60")

    split = max(1, int(len(finished) * (1 - holdout_fraction)))
    train_matches = finished[:split]
    test_matches  = finished[split:]

    # ── Walk-forward Elo + DC sur les données d'entraînement ─────────────────
    elo_state = build_elo_state(sport)
    dc_state      = [None]          # DCModel courant (mutable)
    dc_last_refit = [None]          # date du dernier refit
    dc_x0         = [None]          # warm start

    X_train, y_train, dates_train = _build_rows_walkforward(
        train_matches, sport,
        elo_state, dc_state, dc_last_refit, dc_x0,
        all_past_fn=lambda i: train_matches[:i],
    )

    # ── Walk-forward Elo + DC sur le holdout (continuation) ──────────────────
    X_test, y_test, _ = _build_rows_walkforward(
        test_matches, sport,
        elo_state, dc_state, dc_last_refit, dc_x0,
        all_past_fn=lambda i: train_matches + test_matches[:i],
    )

    if len(X_train) < 40 or len(X_test) < 5:
        raise ValueError(f"Insufficient samples after feature build: train={len(X_train)}, test={len(X_test)}")

    X_train_np = np.array(X_train)
    y_train_np = np.array(y_train)
    X_test_np  = np.array(X_test)
    y_test_np  = np.array(y_test)
    weights    = _sample_weights(dates_train)

    feature_names = LIGUE1_FEATURES if sport == "ligue1" else NBA_FEATURES

    # ── Modèle de validation (métriques holdout) ──────────────────────────────
    val_model = _build_model(sport)
    val_model.fit(X_train_np, y_train_np, sample_weight=weights)

    proba = val_model.predict_proba(X_test_np)
    brier, ll, acc = _compute_metrics(proba, y_test_np, sport)

    # ── Refit final sur 100 % des données ────────────────────────────────────
    # On rejoue le walk-forward complet sur all finished matches
    elo_final  = build_elo_state(sport)
    dc_final   = [None]
    dc_lrf     = [None]
    dc_x0f     = [None]

    X_full, y_full, dates_full = _build_rows_walkforward(
        finished, sport,
        elo_final, dc_final, dc_lrf, dc_x0f,
        all_past_fn=lambda i: finished[:i],
    )

    X_full_np = np.array(X_full)
    y_full_np = np.array(y_full)
    w_full    = _sample_weights(dates_full)

    final_model = _build_model(sport)
    final_model.fit(X_full_np, y_full_np, sample_weight=w_full)

    # DC final : fit sur 100 % des matchs terminés
    dc_for_artifact: DCModel | None = None
    if sport == "ligue1":
        teams_all = sorted({m["home_team_id"] for m in finished} | {m["away_team_id"] for m in finished})
        x0_for_final = dc_model_to_x0(dc_final[0], teams_all) if dc_final[0] else None
        dc_for_artifact = fit_dixon_coles(finished, x0=x0_for_final)

    # ── Sauvegarde de l'artefact final ───────────────────────────────────────
    os.makedirs(os.path.join(model_storage_path, sport), exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(model_storage_path, sport, f"model_{ts}.joblib")
    joblib.dump(
        {
            "model": final_model,
            "feature_names": feature_names,
            "dc_model": dc_for_artifact,
            # L'Elo n'est plus stocké dans l'artefact (recalculé en live dans predict.py)
            "pipeline_version": PIPELINE_VERSION,
        },
        path,
    )

    return TrainResult(
        model=val_model,
        feature_names=feature_names,
        holdout_brier=brier,
        holdout_logloss=ll,
        holdout_accuracy=acc,
        training_samples=len(X_train),
        holdout_samples=len(X_test),
        sport=sport,
        model_path=path,
        X_test=X_test_np,
        y_test=y_test_np,
    )


def load_model(model_path: str) -> dict:
    return joblib.load(model_path)
