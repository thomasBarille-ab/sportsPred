"""Job de collecte de contexte pré-match (météo + blessures + rotations).

Planifié à 18h UTC J-1 (la veille des matchs du lendemain).
Produit les features contextuelles consommées par predict.py le lendemain.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import structlog

from ..db import session
from ..ingestion.news_agent import search_team_context
from ..ingestion.stadium_coords import get_coords
from ..ingestion.weather import fetch_weather

log = structlog.get_logger()

_SPORTS      = ("ligue1", "france_nt")
_HORIZON_H   = 48   # couvre les matchs dans les 48h à venir
_LOOKBACK_H  = 2    # re-traite un match si l'agent a tourné il y a moins de 2h


def run_context_agent(anthropic_api_key: str) -> dict:
    """Fetch météo + contexte LLM pour tous les matchs à venir (Ligue 1 + France NT).

    Upsert dans match_context — idempotent si re-lancé dans la même journée.
    """
    now     = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=_HORIZON_H)

    upcoming = session.fetch_all(
        """
        SELECT f.id, f.sport, f.home_team_id, f.home_team_name,
               f.away_team_id, f.away_team_name, f.match_date,
               mc.agent_run_at
        FROM fixtures f
        LEFT JOIN match_context mc ON mc.fixture_id = f.id
        WHERE f.sport = ANY(%s)
          AND f.status IN ('SCHEDULED', 'TIMED')
          AND f.match_date BETWEEN %s AND %s
        ORDER BY f.match_date
        """,
        (list(_SPORTS), now, horizon),
    )

    n_processed = n_skipped = n_failed = 0

    for fixture in upcoming:
        # Skip si déjà traité aujourd'hui (sauf si re-lancé très récemment)
        last_run = fixture.get("agent_run_at")
        if last_run is not None:
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
            age_h = (now - last_run).total_seconds() / 3600
            if age_h < (24 - _LOOKBACK_H):
                n_skipped += 1
                continue

        fixture_id   = fixture["id"]
        sport        = fixture["sport"]
        home_name    = fixture["home_team_name"]
        away_name    = fixture["away_team_name"]
        match_date   = fixture["match_date"]
        if match_date.tzinfo is None:
            match_date = match_date.replace(tzinfo=timezone.utc)

        log.info(
            "context_agent.processing",
            fixture_id=fixture_id,
            match=f"{home_name} vs {away_name}",
            sport=sport,
        )

        try:
            # ── Météo ──────────────────────────────────────────────────────────
            coords = get_coords(home_name)
            if coords:
                weather = fetch_weather(coords[0], coords[1], match_date)
            else:
                log.debug("context_agent.no_coords", team=home_name)
                weather = {"temp_celsius": 15.0, "rain_mm": 0.0, "wind_kmh": 0.0}

            # ── Blessures / rotations via LLM ──────────────────────────────────
            home_ctx = search_team_context(
                anthropic_api_key, home_name, away_name, match_date, sport
            )
            away_ctx = search_team_context(
                anthropic_api_key, away_name, home_name, match_date, sport
            )

            news_raw = {
                "home": home_ctx.get("summary", ""),
                "away": away_ctx.get("summary", ""),
            }
            llm_analysis = {
                "home_absent_count":    home_ctx["absent_count"],
                "home_rotation_signal": home_ctx["rotation_signal"],
                "away_absent_count":    away_ctx["absent_count"],
                "away_rotation_signal": away_ctx["rotation_signal"],
            }

            session.execute(
                """
                INSERT INTO match_context
                    (fixture_id, agent_run_at,
                     weather_temp_celsius, weather_rain_mm, weather_wind_kmh,
                     home_injuries_count, away_injuries_count,
                     home_rotation_signal, away_rotation_signal,
                     news_raw, llm_analysis)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fixture_id) DO UPDATE SET
                    agent_run_at         = EXCLUDED.agent_run_at,
                    weather_temp_celsius = EXCLUDED.weather_temp_celsius,
                    weather_rain_mm      = EXCLUDED.weather_rain_mm,
                    weather_wind_kmh     = EXCLUDED.weather_wind_kmh,
                    home_injuries_count  = EXCLUDED.home_injuries_count,
                    away_injuries_count  = EXCLUDED.away_injuries_count,
                    home_rotation_signal = EXCLUDED.home_rotation_signal,
                    away_rotation_signal = EXCLUDED.away_rotation_signal,
                    news_raw             = EXCLUDED.news_raw,
                    llm_analysis         = EXCLUDED.llm_analysis
                """,
                (
                    fixture_id,
                    now,
                    weather["temp_celsius"],
                    weather["rain_mm"],
                    weather["wind_kmh"],
                    home_ctx["absent_count"],
                    away_ctx["absent_count"],
                    home_ctx["rotation_signal"],
                    away_ctx["rotation_signal"],
                    json.dumps(news_raw),
                    json.dumps(llm_analysis),
                ),
            )
            n_processed += 1
            log.info(
                "context_agent.done",
                fixture_id=fixture_id,
                match=f"{home_name} vs {away_name}",
                weather_rain=weather["rain_mm"],
                weather_wind=weather["wind_kmh"],
                home_absent=home_ctx["absent_count"],
                away_absent=away_ctx["absent_count"],
                home_rotation=home_ctx["rotation_signal"],
                away_rotation=away_ctx["rotation_signal"],
            )

        except Exception as exc:
            log.error(
                "context_agent.fixture_failed",
                fixture_id=fixture_id,
                error=str(exc),
            )
            n_failed += 1

    log.info(
        "context_agent.finished",
        processed=n_processed,
        skipped=n_skipped,
        failed=n_failed,
    )
    return {"processed": n_processed, "skipped": n_skipped, "failed": n_failed}
