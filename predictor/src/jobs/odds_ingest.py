"""Job d'ingestion des cotes bookmaker via The Odds API.

Planifié à ingestion_hour:30 (30 min après l'ingestion des fixtures)
pour avoir les fixtures fraîches avant de fetcher les cotes correspondantes.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import structlog

from ..db import session
from ..ingestion.odds_api import OddsAPIProvider, OddsRow

log = structlog.get_logger()

# Abréviations connues non déductibles par normalisation simple
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


def _find_fixture_id(
    row: OddsRow,
    fixtures: list[dict],
    window_hours: int = 4,
) -> int | None:
    """Trouve le fixture_id correspondant à un OddsRow par datetime + noms d'équipes."""
    t = row.commence_time
    best_id: int | None = None
    best_score = 0.0

    for f in fixtures:
        if f["sport"] != row.sport:
            continue
        mt = f["match_date"]
        if mt.tzinfo is None:
            mt = mt.replace(tzinfo=timezone.utc)
        if abs((mt - t).total_seconds()) > window_hours * 3600:
            continue
        score = _sim(row.home_team, f["home_team_name"]) + _sim(row.away_team, f["away_team_name"])
        if score > best_score:
            best_score = score
            best_id = f["id"]

    # Les deux noms doivent matcher à ~0.4 chacun en moyenne
    return best_id if best_score >= 0.8 else None


def run_odds_ingest(api_key: str) -> dict:
    if not api_key:
        log.warning("odds_ingest.skipped", reason="ODDS_API_KEY non configurée")
        return {"upserted": 0, "unmatched": 0}

    horizon = datetime.now(timezone.utc) + timedelta(days=7)
    fixtures = session.fetch_all(
        """
        SELECT id, sport, home_team_name, away_team_name, match_date
        FROM fixtures
        WHERE status IN ('SCHEDULED', 'TIMED')
          AND match_date BETWEEN NOW() AND %s
        """,
        (horizon,),
    )

    provider = OddsAPIProvider(api_key)
    upserted = unmatched = 0

    for sport in ("ligue1", "nba"):
        try:
            odds_rows = provider.fetch_upcoming_odds(sport)
        except Exception as exc:
            log.error("odds_ingest.fetch_error", sport=sport, error=str(exc))
            continue

        for row in odds_rows:
            fixture_id = _find_fixture_id(row, fixtures)
            if fixture_id is None:
                unmatched += 1
                log.debug("odds_ingest.unmatched", home=row.home_team, away=row.away_team)
                continue

            session.execute(
                """
                INSERT INTO match_odds
                    (fixture_id, bookmaker, market, odds_home, odds_draw, odds_away, source)
                VALUES (%s, %s, 'h2h', %s, %s, %s, 'odds_api')
                ON CONFLICT (fixture_id, bookmaker, market) DO UPDATE
                  SET odds_home  = EXCLUDED.odds_home,
                      odds_draw  = EXCLUDED.odds_draw,
                      odds_away  = EXCLUDED.odds_away,
                      fetched_at = NOW()
                """,
                (fixture_id, row.bookmaker, row.odds_home, row.odds_draw, row.odds_away),
            )
            upserted += 1

    log.info("odds_ingest.done", upserted=upserted, unmatched=unmatched)
    return {"upserted": upserted, "unmatched": unmatched}
