"""Scheduler APScheduler — orchestre tous les jobs.

Planification UTC :
  06:00 — ingestion Ligue 1 + NBA
  06:30 — ingestion des cotes (The Odds API)
  07:00 — génération des prédictions (+ explications Claude)
  08:00 — évaluation des prédictions (résultats connus)
  09:00 — analyse agent Claude (tool use, patterns d'échec)
  10:00 — résumé Ollama
  03:00 lundi — réentraînement Ligue 1 + NBA
  01/01 03:00 — backfill cotes historiques (idempotent, déclenchable manuellement)
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Settings
from .db import session
from .ingestion.balldontlie import BallDontLieProvider
from .ingestion.football_data import FootballDataProvider
from .jobs import evaluate, ingest, predict, retrain
from .jobs.agent_analysis import run_agent_analysis_job
from .jobs.odds_ingest import run_odds_ingest
from .jobs.odds_backfill import run_odds_backfill
from .jobs.bet_simulation import run_bet_simulation
from .log_capture import capture_steps
from .summaries.ollama import generate_summary

log = structlog.get_logger()


def _log_job(job_name: str, sport: str | None, fn, *args, **kwargs) -> None:
    """Wrapper qui log chaque job dans agent_logs et capture les étapes détaillées."""
    started = datetime.now(timezone.utc)
    log_id = session.execute_returning(
        """
        INSERT INTO agent_logs (job_name, sport, started_at, status)
        VALUES (%s,%s,%s,'running') RETURNING id
        """,
        (job_name, sport, started),
    )
    with capture_steps() as steps:
        try:
            result = fn(*args, **kwargs)
            finished = datetime.now(timezone.utc)
            duration = (finished - started).total_seconds()
            records = None
            if isinstance(result, dict):
                records = result.get("upcoming") or result.get("n_scored") or result.get("n_predicted")
            elif isinstance(result, int):
                records = result

            session.execute(
                """
                UPDATE agent_logs
                SET status = 'success', finished_at = %s, duration_seconds = %s,
                    records_processed = %s, details = %s
                WHERE id = %s
                """,
                (finished, duration, records, json.dumps({"steps": steps}), log_id),
            )
            log.info("job.success", job=job_name, sport=sport, duration=round(duration, 1))
        except Exception as exc:
            finished = datetime.now(timezone.utc)
            duration = (finished - started).total_seconds()
            err = traceback.format_exc()
            session.execute(
                """
                UPDATE agent_logs
                SET status = 'failed', finished_at = %s, duration_seconds = %s,
                    error_message = %s, details = %s
                WHERE id = %s
                """,
                (finished, duration, err[:4000], json.dumps({"steps": steps}), log_id),
            )
            log.error("job.failed", job=job_name, sport=sport, error=str(exc))


def _build_metrics_snapshot() -> dict:
    """Agrège les métriques des 7 derniers jours pour le résumé Ollama.

    Omet les sports sans prédiction évaluée (pas de conversion None → 0).
    """
    rows = session.fetch_all(
        """
        SELECT f.sport,
               COUNT(ps.id)                          AS n_predictions,
               AVG(ps.brier_score)                   AS avg_brier,
               AVG(ps.log_loss)                      AS avg_logloss,
               AVG(ps.is_correct::int::float)         AS accuracy
        FROM fixtures f
        JOIN predictions p  ON p.fixture_id = f.id
        JOIN prediction_scores ps ON ps.prediction_id = p.id
        WHERE f.match_date >= NOW() - INTERVAL '7 days'
        GROUP BY f.sport
        HAVING COUNT(ps.id) > 0
        """,
    )
    result = {}
    for r in rows:
        result[r["sport"]] = {
            k: float(v) for k, v in r.items() if k != "sport" and v is not None
        }
    return result


def _job_bet_simulation() -> None:
    _log_job("bet_simulation", None, run_bet_simulation)


def _job_odds_ingest(cfg: Settings) -> None:
    _log_job("odds_ingest", None, run_odds_ingest, cfg.odds_api_key)


def _job_odds_backfill(cfg: Settings) -> None:
    _log_job("odds_backfill", None, run_odds_backfill)


def _job_ingest(cfg: Settings) -> None:
    fd = FootballDataProvider(cfg.football_data_api_key)
    bdl = BallDontLieProvider(cfg.balldontlie_api_key)
    _log_job("ingest", "ligue1", ingest.run_ingest, fd, "ligue1")
    _log_job("ingest", "nba",    ingest.run_ingest, bdl, "nba")


def _job_predict(cfg: Settings) -> None:
    for sport in ("ligue1", "nba"):
        _log_job("predict", sport, predict.run_predict, sport, cfg.predict_horizon_hours, cfg.anthropic_api_key)


def _job_evaluate() -> None:
    for sport in ("ligue1", "nba"):
        _log_job("evaluate", sport, evaluate.run_evaluate, sport)


def _job_agent_analysis(cfg: Settings) -> None:
    _log_job("agent_analysis", None, run_agent_analysis_job, cfg.anthropic_api_key)


def _job_summary(cfg: Settings) -> None:
    metrics = _build_metrics_snapshot()
    try:
        text = generate_summary(cfg.ollama_url, cfg.ollama_model, metrics)
    except Exception as exc:
        log.error("summary.generate_failed", error=str(exc))
        return

    # Ne sauvegarde pas si Ollama a retourné un message d'erreur
    if text.startswith("["):
        log.warning("summary.ollama_error_response", text=text[:120])
        return

    from datetime import date
    import json
    session.execute(
        """
        INSERT INTO daily_summaries (summary_date, content, metrics_snapshot)
        VALUES (%s,%s,%s)
        ON CONFLICT (summary_date) DO UPDATE SET content = EXCLUDED.content, metrics_snapshot = EXCLUDED.metrics_snapshot
        """,
        (date.today(), text, json.dumps(metrics)),
    )
    log.info("summary.saved", date=str(date.today()))


def _job_retrain(cfg: Settings) -> None:
    for sport in ("ligue1", "nba"):
        _log_job("retrain", sport, retrain.run_retrain, sport, cfg.model_storage_path)


def start_scheduler(cfg: Settings) -> None:
    scheduler = BlockingScheduler(timezone="UTC")

    h = cfg.ingestion_hour_utc
    scheduler.add_job(
        lambda: _job_ingest(cfg),
        CronTrigger(hour=h % 24, minute=0),
        id="ingest", name="Ingestion Ligue1 + NBA",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        lambda: _job_odds_ingest(cfg),
        CronTrigger(hour=h % 24, minute=30),
        id="odds_ingest", name="Ingestion cotes The Odds API",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        lambda: _job_predict(cfg),
        CronTrigger(hour=(h + 1) % 24, minute=0),
        id="predict", name="Génération prédictions",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _job_bet_simulation,
        CronTrigger(hour=(h + 1) % 24, minute=10),
        id="bet_simulation", name="Simulation de paris (EV)",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _job_evaluate,
        CronTrigger(hour=(h + 2) % 24, minute=0),
        id="evaluate", name="Évaluation prédictions",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        lambda: _job_agent_analysis(cfg),
        CronTrigger(hour=(h + 3) % 24, minute=0),
        id="agent_analysis", name="Analyse agent Claude",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        lambda: _job_summary(cfg),
        CronTrigger(hour=(h + 4) % 24, minute=0),
        id="summary", name="Résumé Ollama",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        lambda: _job_retrain(cfg),
        CronTrigger(day_of_week=cfg.retrain_weekday, hour=cfg.retrain_hour_utc, minute=0),
        id="retrain", name="Réentraînement modèles",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        lambda: _job_odds_backfill(cfg),
        CronTrigger(month=1, day=1, hour=3, minute=0),
        id="odds_backfill", name="Backfill historique cotes (one-shot manuel)",
        max_instances=1, coalesce=True,
    )

    from .trigger_server import start_trigger_server
    start_trigger_server(scheduler, cfg)

    log.info("scheduler.started", jobs=[j.id for j in scheduler.get_jobs()])
    scheduler.start()
