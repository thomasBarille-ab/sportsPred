"""Job d'ingestion : récupère les fixtures et résultats des 7 derniers jours
et des 14 prochains jours, les upserte en base.
"""

from __future__ import annotations

import json
from datetime import date, timedelta, timezone

import structlog

from ..db import session
from ..ingestion.base import DataProvider, FixtureDTO

log = structlog.get_logger()


def _upsert_fixture(fx: FixtureDTO) -> None:
    session.execute(
        """
        INSERT INTO fixtures
          (external_id, sport, home_team_id, home_team_name,
           away_team_id, away_team_name, match_date, season,
           competition, status, home_score, away_score)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (external_id, sport) DO UPDATE SET
          status      = EXCLUDED.status,
          home_score  = EXCLUDED.home_score,
          away_score  = EXCLUDED.away_score,
          updated_at  = NOW()
        """,
        (
            fx.external_id, fx.sport,
            fx.home_team_id, fx.home_team_name,
            fx.away_team_id, fx.away_team_name,
            fx.match_date, fx.season,
            fx.competition, fx.status,
            fx.home_score, fx.away_score,
        ),
    )


def run_ingest(provider: DataProvider, sport: str) -> dict:
    today = date.today()
    from_date = today - timedelta(days=7)
    to_date   = today + timedelta(days=14)

    log.info(
        "ingest.start",
        sport=sport,
        fenêtre=f"{from_date} → {to_date}",
        passé_jours=7,
        futur_jours=14,
    )
    n_upcoming = n_results = 0

    try:
        upcoming = provider.fetch_upcoming_fixtures(from_date, to_date)
        for fx in upcoming:
            _upsert_fixture(fx)
        n_upcoming = len(upcoming)
        log.info(
            "ingest.matchs_à_venir",
            sport=sport,
            upsertés=n_upcoming,
            note="fixtures créées ou mises à jour (statut, scores)",
        )

        results = provider.fetch_recent_results(from_date, today)
        n_nouveaux_résultats = 0
        for r in results:
            session.execute(
                """
                UPDATE fixtures
                SET status = 'FINISHED', home_score = %s, away_score = %s, updated_at = NOW()
                WHERE external_id = %s AND sport = %s
                """,
                (r.home_score, r.away_score, r.external_id, r.sport),
            )
            fixture = session.fetch_one(
                "SELECT id FROM fixtures WHERE external_id = %s AND sport = %s",
                (r.external_id, r.sport),
            )
            if not fixture:
                continue
            from ..scoring.metrics import outcome_from_scores
            outcome = outcome_from_scores(r.home_score, r.away_score, sport)
            session.execute(
                """
                INSERT INTO results (fixture_id, home_score, away_score, actual_outcome)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (fixture_id) DO NOTHING
                """,
                (fixture["id"], r.home_score, r.away_score, outcome),
            )
            n_nouveaux_résultats += 1
            log.info(
                "ingest.résultat_enregistré",
                external_id=r.external_id,
                score=f"{r.home_score}-{r.away_score}",
                issue=outcome,
            )
        n_results = len(results)
        log.info(
            "ingest.résultats_récents",
            sport=sport,
            résultats_API=n_results,
            nouveaux_en_base=n_nouveaux_résultats,
        )

    except Exception as exc:
        import httpx
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
            log.error("ingest.auth_error", sport=sport, status=exc.response.status_code,
                      hint="Vérifie la clé API dans les variables d'environnement")
        else:
            log.error("ingest.error", sport=sport, error=str(exc))
        raise

    return {"upcoming": n_upcoming, "results": n_results}


def run_historical_ingest(provider: DataProvider, sport: str, seasons: list[str]) -> int:
    """Chargement initial de l'historique pour une liste de saisons."""
    total = 0
    for season in seasons:
        log.info("ingest.historical", sport=sport, season=season)
        try:
            fixtures = provider.fetch_season_fixtures(season)
        except Exception as exc:
            # 403 = saison non accessible sur le tier actuel — on skip silencieusement
            log.warning("ingest.historical_skip", sport=sport, season=season, reason=str(exc))
            continue
        for fx in fixtures:
            _upsert_fixture(fx)
            if fx.home_score is not None:
                from ..scoring.metrics import outcome_from_scores
                outcome = outcome_from_scores(fx.home_score, fx.away_score, sport)
                f = session.fetch_one(
                    "SELECT id FROM fixtures WHERE external_id = %s AND sport = %s",
                    (fx.external_id, sport),
                )
                if f:
                    session.execute(
                        """
                        INSERT INTO results (fixture_id, home_score, away_score, actual_outcome)
                        VALUES (%s,%s,%s,%s)
                        ON CONFLICT (fixture_id) DO NOTHING
                        """,
                        (f["id"], fx.home_score, fx.away_score, outcome),
                    )
        total += len(fixtures)
        log.info("ingest.historical_done", sport=sport, season=season, count=len(fixtures))
    return total
