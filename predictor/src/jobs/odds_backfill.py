"""Job one-shot : backfill historique des cotes Ligue 1.

Source : football-data.co.uk (CSV gratuit, sans clé API).
Couvre les 4 dernières saisons + saison en cours.
Idempotent — ON CONFLICT DO UPDATE, peut être relancé sans risque.

Déclencher manuellement après le premier déploiement :
  curl -X POST http://predictor:8080/run/odds_backfill -H "X-Internal-Token: ..."
"""

from __future__ import annotations

import datetime
import re
import unicodedata
from difflib import SequenceMatcher

import structlog

from ..db import session
from ..ingestion.football_data_odds import fetch_season_ligue1_odds

log = structlog.get_logger()

_NAME_ALIASES: dict[str, str] = {
    "psg": "paris",
    "paris sg": "paris",
    "paris saint germain": "paris",
    "st etienne": "etienne",
    "saint etienne": "etienne",
}


def _norm(name: str) -> str:
    s = name.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(
        r"\b(fc|sc|as|og|ogc|rc|aj|ac|asc|hsc|hsf|stade|olympique|de|du|le|la|les|saint)\b",
        " ", s,
    )
    s = re.sub(r"[^\w\s]", " ", s)
    s = " ".join(s.split())
    return _NAME_ALIASES.get(s, s)


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def run_odds_backfill() -> dict:
    """Télécharge les CSVs Ligue 1 et insère les cotes historiques en DB."""
    # Charge tous les fixtures ligue1 terminés, indexés par date
    fixtures = session.fetch_all(
        """
        SELECT id, home_team_name, away_team_name, match_date
        FROM fixtures
        WHERE sport = 'ligue1' AND home_score IS NOT NULL
        """
    )

    fixtures_by_date: dict[datetime.date, list[dict]] = {}
    for f in fixtures:
        d = f["match_date"].date() if hasattr(f["match_date"], "date") else f["match_date"]
        fixtures_by_date.setdefault(d, []).append(f)

    current_year = datetime.date.today().year
    seasons = list(range(current_year - 4, current_year + 1))

    upserted = skipped = 0

    for year in seasons:
        try:
            rows = fetch_season_ligue1_odds(year)
        except Exception as exc:
            log.error("odds_backfill.season_error", year=year, error=str(exc))
            continue

        for row in rows:
            # Cherche le fixture correspondant (±1 jour de tolérance timezone)
            fixture_id: int | None = None
            best_score = 0.0

            for delta in (0, 1, -1):
                d = row["date"] + datetime.timedelta(days=delta)
                for f in fixtures_by_date.get(d, []):
                    score = _sim(row["home_team"], f["home_team_name"]) + _sim(row["away_team"], f["away_team_name"])
                    if score > best_score:
                        best_score = score
                        fixture_id = f["id"]

            if fixture_id is None or best_score < 0.8:
                skipped += 1
                log.debug(
                    "odds_backfill.unmatched",
                    date=str(row["date"]),
                    home=row["home_team"],
                    away=row["away_team"],
                    score=round(best_score, 2),
                )
                continue

            session.execute(
                """
                INSERT INTO match_odds
                    (fixture_id, bookmaker, market, odds_home, odds_draw, odds_away, source)
                VALUES (%s, %s, 'h2h', %s, %s, %s, 'football_data_csv')
                ON CONFLICT (fixture_id, bookmaker, market) DO UPDATE
                  SET odds_home  = EXCLUDED.odds_home,
                      odds_draw  = EXCLUDED.odds_draw,
                      odds_away  = EXCLUDED.odds_away,
                      fetched_at = NOW(),
                      source     = 'football_data_csv'
                """,
                (fixture_id, row["bookmaker"], row["odds_home"], row["odds_draw"], row["odds_away"]),
            )
            upserted += 1

    log.info("odds_backfill.done", upserted=upserted, skipped=skipped,
             match_rate=f"{upserted / max(upserted + skipped, 1):.0%}")
    return {"upserted": upserted, "skipped": skipped}
