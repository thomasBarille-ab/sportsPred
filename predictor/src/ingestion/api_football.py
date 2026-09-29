"""Client HTTP API-Football (api-sports.io v3) — Équipe de France.

Tier gratuit : 100 req/jour.
Auth : header x-apisports-key.
Base URL : https://v3.football.api-sports.io
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

_BASE_URL = "https://v3.football.api-sports.io"
FRANCE_TEAM_ID = 2
_RATE_LIMIT_DELAY = 2.5  # secondes entre requêtes (conservateur)

# Mots-clés dans le nom de la compétition → code interne
_COMPETITION_KEYWORDS: list[tuple[str, str]] = [
    ("world cup - qualification", "FIFA_WCQ"),
    ("world cup qualifications", "FIFA_WCQ"),
    ("world cup qualifier", "FIFA_WCQ"),
    ("euro qualification", "UEFA_ECQ"),
    ("european qualification", "UEFA_ECQ"),
    ("nations league", "UEFA_UNL"),
    ("world cup", "FIFA_WC"),
    ("european championship", "UEFA_EC"),
    ("euro cup", "UEFA_EC"),
    ("friendlies", "FRIENDLY"),
    ("friendly", "FRIENDLY"),
    ("amical", "FRIENDLY"),
    ("test event", "FRIENDLY"),
]

# Tags qui signalent une compétition non-senior (à filtrer)
_IRRELEVANT_TAGS = ["u21", "u20", "u19", "u23", "youth", "olympic", "women", "espoirs"]


class ApiFootballClient:
    def __init__(self, api_key: str) -> None:
        self._headers = {"x-apisports-key": api_key}
        self._last_request_at: float = 0.0

    def _get(self, path: str, params: dict | None = None, _retries: int = 0) -> Any:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < _RATE_LIMIT_DELAY:
            time.sleep(_RATE_LIMIT_DELAY - elapsed)

        url = f"{_BASE_URL}{path}"
        log.debug("api_football.request", url=url, params=params)
        resp = httpx.get(url, headers=self._headers, params=params, timeout=30)
        self._last_request_at = time.monotonic()

        if resp.status_code == 429:
            if _retries >= 2:
                log.error("api_football.rate_limited_max_retries")
                resp.raise_for_status()
            retry_after = int(resp.headers.get("Retry-After", "61"))
            log.warning("api_football.rate_limited", retry_after=retry_after, attempt=_retries + 1)
            time.sleep(retry_after + 1)
            return self._get(path, params, _retries=_retries + 1)

        resp.raise_for_status()
        return resp.json()

    def get_france_fixtures(self, season: int) -> list[dict]:
        """Tous les matchs de l'Équipe de France pour une saison calendaire."""
        data = self._get("/fixtures", params={"team": FRANCE_TEAM_ID, "season": season})
        return data.get("response", [])

    def get_fixture_lineups(self, fixture_id: int) -> list[dict]:
        """Compositions officielles pour un match (2 équipes)."""
        data = self._get("/fixtures/lineups", params={"fixture": fixture_id})
        return data.get("response", [])

    @staticmethod
    def map_competition_code(league_name: str) -> str:
        lower = league_name.lower()
        for keyword, code in _COMPETITION_KEYWORDS:
            if keyword in lower:
                return code
        return "FRIENDLY"

    @staticmethod
    def is_relevant(league_name: str) -> bool:
        lower = league_name.lower()
        return not any(tag in lower for tag in _IRRELEVANT_TAGS)
