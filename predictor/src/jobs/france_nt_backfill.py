"""Backfill historique Équipe de France NT.

- Charge les fixtures de 2018 à aujourd'hui (toutes compétitions).
- Pour chaque match terminé sans lineup en base, charge la composition officielle.
- Idempotent : ON CONFLICT DO NOTHING sur tous les upserts.
- Limite max_lineup_fetches par run pour respecter les 100 req/jour API-Football.
"""

from __future__ import annotations

import structlog

from ..db import session
from ..ingestion.france_nt import FranceNTProvider
from .ingest import _upsert_fixture

log = structlog.get_logger()

FRANCE_TEAM_ID = "2"


def _upsert_lineup(fixture_id: int, lineups: list[dict]) -> int:
    """Insère les compositions pour un match. Retourne le nombre de joueurs insérés."""
    inserted = 0
    for team_data in lineups:
        team_id = str(team_data.get("team", {}).get("id", ""))
        france_is_home = team_id == FRANCE_TEAM_ID

        starters = team_data.get("startXI", [])
        subs = team_data.get("substitutes", [])

        for entry in starters:
            p = entry.get("player", {})
            if not p.get("id"):
                continue
            session.execute(
                """
                INSERT INTO france_nt_lineups
                  (fixture_id, player_id, player_name, position, jersey_number,
                   is_starter, france_is_home)
                VALUES (%s,%s,%s,%s,%s, TRUE,%s)
                ON CONFLICT (fixture_id, player_id) DO NOTHING
                """,
                (
                    fixture_id, str(p["id"]), p.get("name", ""),
                    p.get("pos", ""), p.get("number"),
                    france_is_home,
                ),
            )
            inserted += 1

        for entry in subs:
            p = entry.get("player", {})
            if not p.get("id"):
                continue
            session.execute(
                """
                INSERT INTO france_nt_lineups
                  (fixture_id, player_id, player_name, position, jersey_number,
                   is_starter, france_is_home)
                VALUES (%s,%s,%s,%s,%s, FALSE,%s)
                ON CONFLICT (fixture_id, player_id) DO NOTHING
                """,
                (
                    fixture_id, str(p["id"]), p.get("name", ""),
                    p.get("pos", ""), p.get("number"),
                    france_is_home,
                ),
            )

    return inserted


def run_france_nt_backfill(
    provider: FranceNTProvider,
    seasons: list[str],
    max_lineup_fetches: int = 80,
) -> dict:
    """
    seasons : liste d'années calendaires (ex: ['2018','2019',...,'2026']).
    max_lineup_fetches : nombre max de requêtes lineup par run (budget API-Football).
    """
    # ── Étape 1 : fixtures (1 req/saison) ──────────────────────────────────
    total_fixtures = 0
    for season in seasons:
        log.info("france_nt_backfill.season_start", season=season)
        try:
            fixtures = provider.fetch_season_fixtures(season)
        except Exception as exc:
            log.warning("france_nt_backfill.season_error", season=season, error=str(exc))
            continue

        for fx in fixtures:
            _upsert_fixture(fx)
            if fx.home_score is not None:
                from ..scoring.metrics import outcome_from_scores
                outcome = outcome_from_scores(fx.home_score, fx.away_score, "france_nt")
                f = session.fetch_one(
                    "SELECT id FROM fixtures WHERE external_id = %s AND sport = 'france_nt'",
                    (fx.external_id,),
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
        total_fixtures += len(fixtures)
        log.info("france_nt_backfill.season_done", season=season, count=len(fixtures))

    # ── Étape 2 : lineups pour les matchs terminés sans composition ────────
    fixtures_without_lineup = session.fetch_all(
        """
        SELECT f.id, f.external_id, f.home_team_name, f.away_team_name
        FROM fixtures f
        WHERE f.sport = 'france_nt'
          AND f.home_score IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM france_nt_lineups l WHERE l.fixture_id = f.id
          )
        ORDER BY f.match_date DESC
        LIMIT %s
        """,
        (max_lineup_fetches,),
    )

    n_lineups_fetched = 0
    n_players_inserted = 0

    for fx_row in fixtures_without_lineup:
        try:
            raw_lineups = provider.fetch_lineups(fx_row["external_id"])
            if raw_lineups:
                n_inserted = _upsert_lineup(fx_row["id"], raw_lineups)
                n_players_inserted += n_inserted
                n_lineups_fetched += 1
                log.info(
                    "france_nt_backfill.lineup_stored",
                    match=f"{fx_row['home_team_name']} vs {fx_row['away_team_name']}",
                    players=n_inserted,
                )
            else:
                log.debug(
                    "france_nt_backfill.lineup_empty",
                    external_id=fx_row["external_id"],
                )
        except Exception as exc:
            log.warning(
                "france_nt_backfill.lineup_error",
                external_id=fx_row["external_id"],
                error=str(exc),
            )

    # Combien en reste-t-il sans lineup ?
    remaining = session.fetch_one(
        """
        SELECT COUNT(*) AS n FROM fixtures f
        WHERE f.sport = 'france_nt'
          AND f.home_score IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM france_nt_lineups l WHERE l.fixture_id = f.id
          )
        """,
    )
    n_remaining = int((remaining or {}).get("n", 0))

    log.info(
        "france_nt_backfill.done",
        total_fixtures=total_fixtures,
        lineups_fetched=n_lineups_fetched,
        players_inserted=n_players_inserted,
        still_missing_lineups=n_remaining,
        note="Relancer pour charger les lineups restants (budget 100 req/jour API-Football)" if n_remaining else "Toutes les compositions sont chargées",
    )

    return {
        "total_fixtures": total_fixtures,
        "lineups_fetched": n_lineups_fetched,
        "players_inserted": n_players_inserted,
        "still_missing_lineups": n_remaining,
    }
