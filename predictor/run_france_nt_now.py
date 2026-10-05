"""Script one-shot : force le pipeline complet france_nt et affiche la prédiction.

Usage (depuis le container) :
    python run_france_nt_now.py

Séquence :
  1. Migrations DB (idempotent)
  2. Backfill historique si la table france_nt est vide
  3. Ingest fixtures à venir (API-Football)
  4. Retrain si aucun modèle en production
  5. Predict pour les matchs dans les 96h
  6. Affiche les prédictions
"""

from __future__ import annotations

import sys
import os

# Assure que le package predictor/src est trouvé
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import logging
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%H:%M:%S"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
)
logging.basicConfig(stream=sys.stdout, level=logging.INFO)

from src.config import load_settings
from src.db import session

cfg = load_settings()
session.init_pool(cfg.postgres_url)

log = structlog.get_logger()

# ── 1. Migrations ─────────────────────────────────────────────────────────────
log.info("step.migrations")
from src.db.migrate import run_migrations
run_migrations()

# ── 2. Backfill historique si moins de 15 matchs terminés ─────────────────────
finished = session.fetch_one(
    "SELECT COUNT(*) AS n FROM fixtures WHERE sport = 'france_nt' AND home_score IS NOT NULL"
)
n_finished = finished["n"] if finished else 0
if n_finished < 15:
    import datetime
    from src.ingestion.france_nt import FranceNTProvider
    from src.jobs.france_nt_backfill import run_france_nt_backfill

    log.info("backfill.start", n_finished_current=n_finished,
             message="Chargement historique (API-Football si dispo, sinon TheSportsDB)")
    provider = FranceNTProvider(cfg.apifootball_api_key or "", cfg.football_data_api_key)
    current_year = datetime.date.today().year
    seasons = [str(y) for y in range(2018, current_year + 1)]
    run_france_nt_backfill(provider, seasons, max_lineup_fetches=80)
    log.info("backfill.done")
else:
    log.info("backfill.skip", matchs_terminés=n_finished)

# ── 3. Ingest fixtures à venir (API-Football ou TheSportsDB en fallback) ──────
log.info("ingest.start")
from src.ingestion.france_nt import FranceNTProvider
from src.jobs.ingest import run_france_nt_ingest_and_lineups
provider = FranceNTProvider(cfg.apifootball_api_key or "", cfg.football_data_api_key)
try:
    result = run_france_nt_ingest_and_lineups(provider, "france_nt")
    log.info("ingest.done", **result)
except Exception as exc:
    log.warning("ingest.failed", error=str(exc))

# ── 4. Retrain si aucun modèle en production ──────────────────────────────────
prod_model = session.fetch_one(
    "SELECT id, version, holdout_brier FROM model_versions WHERE sport = 'france_nt' AND is_production = TRUE LIMIT 1"
)
if not prod_model:
    finished = session.fetch_one(
        "SELECT COUNT(*) AS n FROM fixtures WHERE sport = 'france_nt' AND home_score IS NOT NULL"
    )
    n_finished = finished["n"] if finished else 0
    if n_finished >= 2:
        log.info("retrain.start", matchs_terminés=n_finished, note="cold start si < 15 matchs")
        from src.jobs.retrain import run_retrain
        result = run_retrain("france_nt", cfg.model_storage_path)
        if result.get("status") == "success":
            log.info("retrain.done", version=result["version"], brier=round(result["holdout_brier"], 4))
        else:
            log.error("retrain.failed", **result)
            sys.exit(1)
    else:
        log.error(
            "retrain.insufficient_data",
            matchs_terminés=n_finished,
            minimum_requis=2,
            message="Il faut au moins 2 matchs terminés en base.",
        )
        sys.exit(1)
else:
    log.info("model.ok", version=prod_model["version"], brier=round(prod_model["holdout_brier"], 4))

# ── 5. Predict (96h d'horizon pour être sûr de capturer le prochain match) ────
log.info("predict.start", horizon_hours=96)
from src.jobs.predict import run_predict
n = run_predict("france_nt", horizon_hours=96, anthropic_api_key=cfg.anthropic_api_key)
log.info("predict.done", nouvelles_prédictions=n)

# ── 6. Affiche les prédictions ────────────────────────────────────────────────
preds = session.fetch_all(
    """
    SELECT f.home_team_name, f.away_team_name, f.match_date, f.competition,
           p.predicted_outcome, p.prob_home_win, p.prob_draw, p.prob_away_win,
           GREATEST(p.prob_home_win, COALESCE(p.prob_draw, 0), p.prob_away_win) AS confidence
    FROM predictions p
    JOIN fixtures f ON f.id = p.fixture_id
    WHERE f.sport = 'france_nt'
      AND f.match_date >= NOW()
    ORDER BY f.match_date
    LIMIT 5
    """
)

if not preds:
    log.warning("predict.no_results", message="Aucune prédiction générée — vérifiez les fixtures à venir en DB")
    sys.exit(0)

_OUTCOME = {"home": "Victoire domicile", "draw": "Match nul", "away": "Victoire extérieur"}
_COMP    = {
    "FRIENDLY": "Amical", "UEFA_UNL": "Nations League",
    "FIFA_WCQ": "Qualif. CM", "UEFA_ECQ": "Qualif. Euro",
    "FIFA_WC": "Coupe du Monde", "UEFA_EC": "Euro",
}

print()
print("=" * 60)
print("  PRÉDICTIONS FRANCE NT")
print("=" * 60)
for p in preds:
    comp    = _COMP.get(p["competition"] or "FRIENDLY", p["competition"] or "")
    outcome = _OUTCOME.get(p["predicted_outcome"], p["predicted_outcome"])
    d       = p["match_date"].strftime("%d/%m/%Y %H:%M UTC") if p["match_date"] else "?"
    ph      = float(p["prob_home_win"] or 0)
    pd_     = float(p["prob_draw"] or 0)
    pa      = float(p["prob_away_win"] or 0)
    conf    = float(p["confidence"] or 0)
    print()
    print(f"  [{comp}]  {d}")
    print(f"  {p['home_team_name']}  vs  {p['away_team_name']}")
    print(f"  → {outcome}  (confiance {conf:.0%})")
    print(f"     Dom {ph:.0%}  |  Nul {pd_:.0%}  |  Ext {pa:.0%}")
print()
print("=" * 60)
