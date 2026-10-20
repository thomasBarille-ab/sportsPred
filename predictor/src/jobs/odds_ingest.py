"""Job d'ingestion des cotes bookmaker via The Odds API.

Planifié à ingestion_hour:30 (30 min après l'ingestion des fixtures)
pour avoir les fixtures fraîches avant de fetcher les cotes correspondantes.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

import structlog

from ..db import session
from ..ingestion.odds_api import MarketOddsRow, OddsAPIProvider, OddsRow

# Fenêtres pour décider si on fetch les cotes pour un sport donné.
# But : économiser le quota Odds API quand un sport n'a aucun match prévu
# (France NT joue ~1x/mois, NBA/Ligue 1 ont des offseasons).
_OPENING_LOOKAHEAD_DAYS = 7
_CLOSING_LOOKAHEAD_HOURS = 24

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
    row: OddsRow | MarketOddsRow,
    fixtures: list[dict],
    window_hours: int = 4,
) -> int | None:
    """Trouve le fixture_id correspondant à un row par datetime + noms d'équipes.

    NB : balldontlie v1 stocke les fixtures NBA à minuit UTC de la date US ET
    du match (l'heure réelle est dans le champ `status`, pas parsée). L'écart
    avec le commence_time réel de l'Odds API peut atteindre ~30h. On élargit
    donc la fenêtre pour NBA et on compte sur la similarité des noms d'équipes
    pour désambiguïser deux matchs voisins.
    """
    t = row.commence_time
    effective_window = 30 if row.sport == "nba" else window_hours
    best_id: int | None = None
    best_score = 0.0

    for f in fixtures:
        if f["sport"] != row.sport:
            continue
        mt = f["match_date"]
        if mt.tzinfo is None:
            mt = mt.replace(tzinfo=UTC)
        if abs((mt - t).total_seconds()) > effective_window * 3600:
            continue
        score = _sim(row.home_team, f["home_team_name"]) + _sim(row.away_team, f["away_team_name"])
        if score > best_score:
            best_score = score
            best_id = f["id"]

    # Les deux noms doivent matcher à ~0.4 chacun en moyenne
    return best_id if best_score >= 0.8 else None


def run_odds_ingest(api_key: str, closing: bool = False) -> dict:
    if not api_key:
        log.warning("odds_ingest.skipped", reason="ODDS_API_KEY non configurée")
        return {"upserted": 0, "unmatched": 0}

    # Opening : horizon large pour fetcher les cotes des prochains matchs.
    # Closing : horizon court (matchs du jour) car le but est le CLV.
    now = datetime.now(UTC)
    if closing:
        horizon = now + timedelta(hours=_CLOSING_LOOKAHEAD_HOURS)
    else:
        horizon = now + timedelta(days=_OPENING_LOOKAHEAD_DAYS)

    fixtures = session.fetch_all(
        """
        SELECT id, sport, home_team_name, away_team_name, match_date
        FROM fixtures
        WHERE status IN ('SCHEDULED', 'TIMED')
          AND match_date BETWEEN NOW() AND %s
        """,
        (horizon,),
    )

    # Si aucun match dans la fenêtre pour un sport, on skip son appel API —
    # évite de brûler du quota (France NT surtout, qui joue ~1x/mois).
    fixtures_by_sport: dict[str, list[dict]] = defaultdict(list)
    for f in fixtures:
        fixtures_by_sport[f["sport"]].append(f)

    provider = OddsAPIProvider(api_key)
    upserted = upserted_markets = unmatched = skipped_sports = 0

    for sport in ("ligue1", "nba", "france_nt"):
        if not fixtures_by_sport.get(sport):
            log.info("odds_ingest.skipped_sport",
                     sport=sport, closing=closing, reason="aucun match dans la fenêtre")
            skipped_sports += 1
            continue

        # À l'ouverture on étend les markets : Ligue 1 (h2h+totals+btts), NBA (h2h+totals+spreads).
        # À la clôture on ne garde que h2h (CLV). Chaque market = 1 req au quota Odds API.
        if closing:
            markets = "h2h"
        elif sport == "ligue1":
            markets = "h2h,totals,btts"
        elif sport == "nba":
            markets = "h2h,totals,spreads"
        else:
            markets = "h2h"

        try:
            h2h_rows, market_rows = provider.fetch_upcoming_odds(sport, markets=markets)
        except Exception as exc:
            log.error("odds_ingest.fetch_error", sport=sport, error=str(exc))
            continue

        for row in h2h_rows:
            fixture_id = _find_fixture_id(row, fixtures)
            if fixture_id is None:
                unmatched += 1
                log.debug("odds_ingest.unmatched", home=row.home_team, away=row.away_team)
                continue

            session.execute(
                """
                INSERT INTO match_odds
                    (fixture_id, bookmaker, market, odds_home, odds_draw, odds_away, source, closing)
                VALUES (%s, %s, 'h2h', %s, %s, %s, 'odds_api', %s)
                ON CONFLICT (fixture_id, bookmaker, market, closing) DO UPDATE
                  SET odds_home  = EXCLUDED.odds_home,
                      odds_draw  = EXCLUDED.odds_draw,
                      odds_away  = EXCLUDED.odds_away,
                      fetched_at = NOW()
                """,
                (fixture_id, row.bookmaker, row.odds_home, row.odds_draw, row.odds_away, closing),
            )
            upserted += 1

        for mrow in market_rows:
            fixture_id = _find_fixture_id(mrow, fixtures)
            if fixture_id is None:
                unmatched += 1
                continue

            session.execute(
                """
                INSERT INTO match_odds_markets
                    (fixture_id, bookmaker, market, selection, line, odds, source, closing)
                VALUES (%s, %s, %s, %s, %s, %s, 'odds_api', %s)
                ON CONFLICT (fixture_id, bookmaker, market, selection, line, closing) DO UPDATE
                  SET odds = EXCLUDED.odds, fetched_at = NOW()
                """,
                (fixture_id, mrow.bookmaker, mrow.market, mrow.selection,
                 mrow.line, mrow.odds, closing),
            )
            upserted_markets += 1

    log.info(
        "odds_ingest.done",
        upserted_h2h=upserted,
        upserted_markets=upserted_markets,
        unmatched=unmatched,
        skipped_sports=skipped_sports,
        closing=closing,
    )
    return {
        "upserted": upserted,
        "upserted_markets": upserted_markets,
        "unmatched": unmatched,
        "skipped_sports": skipped_sports,
    }
