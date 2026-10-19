"""Client The Odds API v4 — cotes en temps réel pour Ligue 1, NBA et France NT.

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

# france_nt couvre plusieurs compétitions → liste de sport_keys
SPORT_KEYS: dict[str, str | list[str]] = {
    "ligue1": "soccer_france_ligue_one",
    "nba": "basketball_nba",
    "france_nt": [
        "soccer_international_friendlies",
        "soccer_europe_nations_league",
        "soccer_world_cup_qual_europe",
        "soccer_euro_qualification",
        "soccer_fifa_world_cup",
        "soccer_uefa_european_championship",
    ],
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


@dataclass
class MarketOddsRow:
    """Cote pour un market non-h2h (totals, btts, …). Une ligne par sélection."""
    sport: str
    home_team: str
    away_team: str
    commence_time: datetime
    bookmaker: str
    market: str        # 'ou_total', 'btts'
    selection: str     # 'over','under','yes','no'
    line: float        # 2.5 pour O/U, 0 pour BTTS
    odds: float


class OddsAPIProvider:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _fetch_sport_key(
        self,
        sport: str,
        sport_key: str,
        markets: str = "h2h",
    ) -> tuple[list[OddsRow], list[MarketOddsRow]]:
        """Fetch odds for a single sport_key. Chaque market demandé compte 1 req au quota."""
        try:
            resp = httpx.get(
                f"{_BASE_URL}/sports/{sport_key}/odds",
                params={
                    "apiKey": self._api_key,
                    "regions": "eu",
                    "markets": markets,
                    "oddsFormat": "decimal",
                    "bookmakers": ",".join(_PREFERRED_BOOKMAKERS),
                },
                timeout=30,
            )
            remaining = resp.headers.get("x-requests-remaining", "?")
            used = resp.headers.get("x-requests-used", "?")
            log.info(
                "odds_api.fetch",
                sport=sport,
                sport_key=sport_key,
                markets=markets,
                quota_remaining=remaining,
                quota_used=used,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error("odds_api.http_error", status=exc.response.status_code, sport=sport, sport_key=sport_key)
            raise

        h2h_results: list[OddsRow] = []
        market_results: list[MarketOddsRow] = []

        for event in resp.json():
            commence_time = datetime.fromisoformat(event["commence_time"])
            home_team = event["home_team"]
            away_team = event["away_team"]

            for bm in event.get("bookmakers", []):
                for mkt in bm.get("markets", []):
                    key = mkt["key"]
                    outcomes = mkt.get("outcomes", [])
                    if key == "h2h":
                        self._parse_h2h(
                            sport, home_team, away_team, commence_time,
                            bm["key"], outcomes, h2h_results,
                        )
                    elif key == "totals":
                        self._parse_totals(
                            sport, home_team, away_team, commence_time,
                            bm["key"], outcomes, market_results,
                        )
                    elif key == "btts":
                        self._parse_btts(
                            sport, home_team, away_team, commence_time,
                            bm["key"], outcomes, market_results,
                        )

        return h2h_results, market_results

    @staticmethod
    def _parse_h2h(sport, home_team, away_team, commence_time, bookmaker, outcomes, out):
        prices = {o["name"]: o["price"] for o in outcomes}
        odds_home = prices.get(home_team)
        odds_away = prices.get(away_team)
        if odds_home is None or odds_away is None:
            return
        odds_draw_raw = prices.get("Draw")
        out.append(OddsRow(
            sport=sport,
            home_team=home_team,
            away_team=away_team,
            commence_time=commence_time,
            bookmaker=bookmaker,
            odds_home=float(odds_home),
            odds_draw=float(odds_draw_raw) if odds_draw_raw else None,
            odds_away=float(odds_away),
        ))

    @staticmethod
    def _parse_totals(sport, home_team, away_team, commence_time, bookmaker, outcomes, out):
        # Un bookmaker peut proposer plusieurs lines (2.5, 3.5…). On garde toutes.
        for o in outcomes:
            name = o.get("name", "").lower()
            point = o.get("point")
            price = o.get("price")
            if name not in ("over", "under") or point is None or price is None:
                continue
            out.append(MarketOddsRow(
                sport=sport,
                home_team=home_team,
                away_team=away_team,
                commence_time=commence_time,
                bookmaker=bookmaker,
                market="ou_total",
                selection=name,
                line=float(point),
                odds=float(price),
            ))

    @staticmethod
    def _parse_btts(sport, home_team, away_team, commence_time, bookmaker, outcomes, out):
        for o in outcomes:
            name = o.get("name", "").lower()
            price = o.get("price")
            if name not in ("yes", "no") or price is None:
                continue
            out.append(MarketOddsRow(
                sport=sport,
                home_team=home_team,
                away_team=away_team,
                commence_time=commence_time,
                bookmaker=bookmaker,
                market="btts",
                selection=name,
                line=0.0,
                odds=float(price),
            ))

    def fetch_upcoming_odds(
        self,
        sport: str,
        markets: str = "h2h",
    ) -> tuple[list[OddsRow], list[MarketOddsRow]]:
        """Retourne (h2h_rows, market_rows) pour tous les prochains matchs du sport."""
        key_or_list = SPORT_KEYS.get(sport)
        if not key_or_list:
            raise ValueError(f"Sport non supporté par The Odds API : {sport!r}")

        sport_keys = key_or_list if isinstance(key_or_list, list) else [key_or_list]

        h2h_results: list[OddsRow] = []
        market_results: list[MarketOddsRow] = []
        for sk in sport_keys:
            try:
                h2h, mkts = self._fetch_sport_key(sport, sk, markets=markets)
                h2h_results.extend(h2h)
                market_results.extend(mkts)
            except httpx.HTTPStatusError:
                # Erreur sur une compétition ne bloque pas les autres
                continue

        n_events = len({(r.home_team, r.away_team) for r in h2h_results})
        log.info(
            "odds_api.parsed",
            sport=sport,
            events=n_events,
            h2h_rows=len(h2h_results),
            market_rows=len(market_results),
        )
        return h2h_results, market_results
