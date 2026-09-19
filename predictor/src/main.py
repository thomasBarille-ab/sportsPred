"""Point d'entrée du container predictor.

Séquence de démarrage :
  1. Charge la config depuis les variables d'environnement
  2. Initialise le pool de connexions Postgres
  3. Lance un chargement historique si la DB est vide (bootstrap)
  4. Démarre le scheduler APScheduler (bloquant)
"""

from __future__ import annotations

import logging
import sys

import structlog

from .config import Settings, load_settings
from .db import session
from .log_capture import capture_processor


def _configure_logging(level: str) -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            capture_processor,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    logging.basicConfig(stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO))


def _bootstrap_if_empty(cfg: Settings) -> None:
    """Si aucune fixture n'existe en DB, charge 2 saisons d'historique."""
    import httpx
    from .ingestion.balldontlie import BallDontLieProvider
    from .ingestion.football_data import FootballDataProvider
    from .jobs.ingest import run_historical_ingest

    log = structlog.get_logger()

    ligue1_count = session.fetch_one("SELECT COUNT(*) AS n FROM fixtures WHERE sport = 'ligue1'")
    nba_count    = session.fetch_one("SELECT COUNT(*) AS n FROM fixtures WHERE sport = 'nba'")

    if ligue1_count and ligue1_count["n"] == 0:
        log.info("bootstrap.ligue1.start")
        fd = FootballDataProvider(cfg.football_data_api_key)
        try:
            run_historical_ingest(fd, "ligue1", ["2022", "2023", "2024"])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                log.error(
                    "bootstrap.ligue1.auth_error",
                    status=exc.response.status_code,
                    hint="Vérifie FOOTBALL_DATA_API_KEY dans .env",
                )
            else:
                raise
        else:
            log.info("bootstrap.ligue1.done")

    if nba_count and nba_count["n"] == 0:
        log.info("bootstrap.nba.start")
        bdl = BallDontLieProvider(cfg.balldontlie_api_key)
        try:
            run_historical_ingest(bdl, "nba", ["2022", "2023", "2024"])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                log.error(
                    "bootstrap.nba.auth_error",
                    status=exc.response.status_code,
                    hint="Vérifie BALLDONTLIE_API_KEY dans .env — le token est obligatoire",
                )
            else:
                raise
        else:
            log.info("bootstrap.nba.done")


def _maybe_retrain_if_no_model(cfg: Settings) -> None:
    """Lance le retrain si aucun modèle en production et assez de données."""
    from .jobs.retrain import run_retrain

    log = structlog.get_logger()

    for sport in ("ligue1", "nba"):
        prod_model = session.fetch_one(
            "SELECT id FROM model_versions WHERE sport = %s AND is_production = TRUE LIMIT 1",
            (sport,),
        )
        if prod_model:
            continue
        count = session.fetch_one(
            "SELECT COUNT(*) AS n FROM fixtures WHERE sport = %s AND home_score IS NOT NULL",
            (sport,),
        )
        if count and count["n"] >= 60:
            log.info(
                "bootstrap.auto_retrain",
                sport=sport,
                matchs=count["n"],
                reason="Aucun modèle en production — lancement immédiat du retrain",
            )
            try:
                run_retrain(sport, cfg.model_storage_path)
            except Exception as exc:
                log.error("bootstrap.retrain_failed", sport=sport, error=str(exc))


def main() -> None:
    cfg: Settings = load_settings()
    _configure_logging(cfg.log_level)
    log = structlog.get_logger()

    log.info("predictor.starting")
    session.init_pool(cfg.postgres_url)

    _bootstrap_if_empty(cfg)
    _maybe_retrain_if_no_model(cfg)

    # Démarre le scheduler (bloquant — ne retourne jamais)
    from .scheduler import start_scheduler
    start_scheduler(cfg)


if __name__ == "__main__":
    main()
