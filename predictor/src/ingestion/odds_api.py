"""Client The Odds API v4 — cotes en temps réel pour Ligue 1 et NBA.

Doc : https://the-odds-api.com/liveapi/guides/v4/
Tier gratuit : 500 requêtes/mois (chaque match = 1 requête).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx
import structlog

log = structlog.get_logger()

_BASE_URL = "https://api.the-odds-api.com/v4"

SPORT_KEYS: dict[str, str] = {
    "ligue1": "soccer_france_ligue_one",
    "nba": "basketball_nba",
}

# Ordre de préférence — Pinnacle est le bookmaker le plus "sharp" (meilleures implied proba)
_PREFERRED_BOOKMAKERS = ["pinnacle", "bet365", "williamhill", "unibet", "betfair_ex_eu"]


@dataclass
class OddsRow:
    sport: str
    home_team: str
    away_team: str
    commence_time: datetime
    bookmaker: str
    odds_home: float
    odds_draw: float | None  # None pour NBA
    odds_away: float


class OddsAPIProvider:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def fetch_upcoming_odds(self, sport: str) -> list[OddsRow]:
        """Retourne les cotes de tous les prochains matchs du sport."""
        sport_key = SPORT_KEYS.get(sport)
        if not sport_key:
            raise ValueError(f"Sport non supporté par The Odds API : {sport!r}")

        try:
            resp = httpx.get(
                f"{_BASE_URL}/sports/{sport_key}/odds",
                params={
                    "apiKey": self._api_key,
                    "regions": "eu",
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                    "bookmakers": ",".join(_PREFERRED_BOOKMAKERS),
                },
                timeout=30,
            )
            remaining = resp.headers.get("x-requests-remaining", "?")
            used = resp.headers.get("x-requests-used", "?")
            log.info("odds_api.fetch", sport=sport, quota_remaining=remaining, quota_used=used)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error("odds_api.http_error", status=exc.response.status_code, sport=sport)
            raise

        results: list[OddsRow] = []
        for event in resp.json():
            commence_time = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
            home_team = event["home_team"]
            away_team = event["away_team"]

            for bm in event.get("bookmakers", []):
                for market in bm.get("markets", []):
                    if market["key"] != "h2h":
                        continue
                    outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
                    odds_home = outcomes.get(home_team)
                    odds_away = outcomes.get(away_team)
                    if odds_home is None or odds_away is None:
                        continue
                    odds_draw_raw = outcomes.get("Draw")
                    results.append(OddsRow(
                        sport=sport,
                        home_team=home_team,
                        away_team=away_team,
                        commence_time=commence_time,
                        bookmaker=bm["key"],
                        odds_home=float(odds_home),
                        odds_draw=float(odds_draw_raw) if odds_draw_raw else None,
                        odds_away=float(odds_away),
                    ))

        n_events = len({(r.home_team, r.away_team) for r in results})
        log.info("odds_api.parsed", sport=sport, events=n_events, rows=len(results))
        return results
