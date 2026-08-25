"""Entraînement XGBoost avec holdout chronologique et pondération temporelle.

Ligue 1 : 3 classes (0=home win, 1=draw, 2=away win) — multi:softprob
NBA      : 2 classes (0=away win, 1=home win) — binary:logistic

Holdout : les 15% les plus récents sont utilisés pour l'évaluation uniquement.
Temporal weighting : les matchs récents sont pondérés plus fortement
  (demi-vie de 365 jours).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import joblib
import numpy as np
import xgboost as xgb
from sklearn.metrics import brier_score_loss, log_loss

from ..features.builder import (
    LIGUE1_FEATURES,
    NBA_FEATURES,
    build_features_ligue1,
    build_features_nba,
)
from ..features.dixon_coles import DCModel, fit_dixon_coles
from ..features.elo import EloState, compute_elo_ratings


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


def train_model(
    sport: Literal["ligue1", "nba"],
    all_matches: list[dict],
    model_storage_path: str,
    holdout_fraction: float = 0.15,
) -> TrainResult:
    """Entraîne un nouveau modèle et retourne les métriques sur le holdout."""

    finished = sorted(
        [m for m in all_matches if m.get("home_score") is not None],
        key=lambda x: x["match_date"],
    )
    if len(finished) < 60:
        raise ValueError(f"Not enough finished matches for {sport}: {len(finished)} < 60")

    split = max(1, int(len(finished) * (1 - holdout_fraction)))
    train_matches = finished[:split]
    test_matches  = finished[split:]

    # ── Feature engineering ──────────────────────────────────────────────────
    # Elo et DC sont fitted sur train_matches uniquement pour éviter le leakage
    elo_ratings = compute_elo_ratings(train_matches, sport)
    from ..features.elo import build_elo_state
    elo_state = build_elo_state(sport)
    elo_state.ratings = elo_ratings

    dc_model: DCModel | None = None
    if sport == "ligue1":
        dc_model = fit_dixon_coles(train_matches)

    def build_row(m: dict, past_matches: list[dict]) -> list[float] | None:
        try:
            if sport == "ligue1":
                vec, _ = build_features_ligue1(
                    m["home_team_id"], m["away_team_id"],
                    m["match_date"], past_matches,
                    elo_state, dc_model,
                )
            else:
                vec, _ = build_features_nba(
                    m["home_team_id"], m["away_team_id"],
                    m["match_date"], past_matches,
                    elo_state,
                )
            return vec
        except Exception:
            return None

    X_train, y_train, dates_train = [], [], []
    for i, m in enumerate(train_matches):
        past = train_matches[:i]  # n'utilise que les matchs passés
        row = build_row(m, past)
        if row is None:
            continue
        X_train.append(row)
        y_train.append(_outcome_label(m["home_score"], m["away_score"], sport))
        dates_train.append(m["match_date"])

    X_test, y_test = [], []
    for m in test_matches:
        row = build_row(m, train_matches)  # toutes les données d'entraînement disponibles
        if row is None:
            continue
        X_test.append(row)
        y_test.append(_outcome_label(m["home_score"], m["away_score"], sport))

    if len(X_train) < 40 or len(X_test) < 5:
        raise ValueError(f"Insufficient samples after feature build: train={len(X_train)}, test={len(X_test)}")

    X_train_np = np.array(X_train)
    y_train_np = np.array(y_train)
    weights    = _sample_weights(dates_train)

    # ── Modèle ───────────────────────────────────────────────────────────────
    if sport == "ligue1":
        model = xgb.XGBClassifier(
            objective="multi:softprob",
            num_class=3,
            n_estimators=400,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="mlogloss",
            random_state=42,
            n_jobs=-1,
        )
        feature_names = LIGUE1_FEATURES
    else:
        model = xgb.XGBClassifier(
            objective="binary:logistic",
            n_estimators=400,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )
        feature_names = NBA_FEATURES

    model.fit(X_train_np, y_train_np, sample_weight=weights)

    # ── Métriques holdout ────────────────────────────────────────────────────
    X_test_np = np.array(X_test)
    y_test_np = np.array(y_test)
    proba = model.predict_proba(X_test_np)

    if sport == "ligue1":
        # Brier multi-class : moyenne des scores binaires par classe
        from sklearn.preprocessing import label_binarize
        y_bin = label_binarize(y_test_np, classes=[0, 1, 2])
        brier = float(np.mean([
            brier_score_loss(y_bin[:, k], proba[:, k]) for k in range(3)
        ]))
        ll = float(log_loss(y_test_np, proba, labels=[0, 1, 2]))
        acc = float(np.mean(np.argmax(proba, axis=1) == y_test_np))
    else:
        brier = float(brier_score_loss(y_test_np, proba[:, 1]))
        ll    = float(log_loss(y_test_np, proba))
        acc   = float(np.mean((proba[:, 1] >= 0.5).astype(int) == y_test_np))

    # ── Sauvegarde ───────────────────────────────────────────────────────────
    os.makedirs(os.path.join(model_storage_path, sport), exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(model_storage_path, sport, f"model_{ts}.joblib")
    joblib.dump({"model": model, "feature_names": feature_names, "dc_model": dc_model, "elo": elo_state}, path)

    return TrainResult(
        model=model,
        feature_names=feature_names,
        holdout_brier=brier,
        holdout_logloss=ll,
        holdout_accuracy=acc,
        training_samples=len(X_train),
        holdout_samples=len(X_test),
        sport=sport,
    )


def load_model(model_path: str) -> dict:
    return joblib.load(model_path)
