"""Provider football-data.org — Ligue 1 (code compétition : FL1).

Tier gratuit : 10 req/min, scores différés de ~2 min.
Doc : https://www.football-data.org/documentation/quickstart
"""

import time
from datetime import date, datetime, timezone
from typing import Any

import httpx
import structlog

from .base import DataProvider, FixtureDTO, ResultDTO

log = structlog.get_logger()

_BASE_URL = "https://api.football-data.org/v4"
_COMPETITION = "FL1"
_SPORT = "ligue1"
_RATE_LIMIT_DELAY = 6.5  # secondes entre requêtes (10 req/min = 6s; marge de sécurité)


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class FootballDataProvider(DataProvider):
    def __init__(self, api_key: str) -> None:
        self._headers = {"X-Auth-Token": api_key}
        self._last_request_at: float = 0.0

    def _get(self, path: str, params: dict | None = None, _retries: int = 0) -> Any:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < _RATE_LIMIT_DELAY:
            time.sleep(_RATE_LIMIT_DELAY - elapsed)

        url = f"{_BASE_URL}{path}"
        log.debug("football_data.request", url=url, params=params)
        resp = httpx.get(url, headers=self._headers, params=params, timeout=30)
        self._last_request_at = time.monotonic()

        if resp.status_code == 429:
            if _retries >= 2:
                log.error("football_data.rate_limited_max_retries", retries=_retries + 1)
                resp.raise_for_status()
            retry_after = int(resp.headers.get("Retry-After", "61"))
            log.warning("football_data.rate_limited", retry_after=retry_after, attempt=_retries + 1)
            time.sleep(retry_after + 1)
            return self._get(path, params, _retries=_retries + 1)

        resp.raise_for_status()
        return resp.json()

    def _parse_match(self, m: dict) -> FixtureDTO:
        score = m.get("score", {})
        ft = score.get("fullTime", {})
        home_score = ft.get("home")
        away_score = ft.get("away")
        status = m.get("status", "SCHEDULED")

        # football-data retourne la saison comme {"startDate": "2024-08-...", ...}
        season_obj = m.get("season", {})
        season_year = str(season_obj.get("startDate", ""))[:4] or "unknown"

        return FixtureDTO(
            external_id=str(m["id"]),
            sport=_SPORT,
            home_team_id=str(m["homeTeam"]["id"]),
            home_team_name=m["homeTeam"]["name"],
            away_team_id=str(m["awayTeam"]["id"]),
            away_team_name=m["awayTeam"]["name"],
            match_date=_parse_date(m.get("utcDate")) or datetime.now(timezone.utc),
            season=season_year,
            competition=_COMPETITION,
            status=status,
            home_score=home_score,
            away_score=away_score,
        )

    def fetch_upcoming_fixtures(self, from_date: date, to_date: date) -> list[FixtureDTO]:
        data = self._get(
            f"/competitions/{_COMPETITION}/matches",
            params={
                "status": "SCHEDULED,TIMED,POSTPONED,SUSPENDED",
                "dateFrom": from_date.isoformat(),
                "dateTo": to_date.isoformat(),
            },
        )
        return [self._parse_match(m) for m in data.get("matches", [])]

    def fetch_recent_results(self, from_date: date, to_date: date) -> list[ResultDTO]:
        data = self._get(
            f"/competitions/{_COMPETITION}/matches",
            params={
                "status": "FINISHED",
                "dateFrom": from_date.isoformat(),
                "dateTo": to_date.isoformat(),
            },
        )
        out: list[ResultDTO] = []
        for m in data.get("matches", []):
            score = m.get("score", {}).get("fullTime", {})
            hs = score.get("home")
            as_ = score.get("away")
            if hs is None or as_ is None:
                continue
            out.append(ResultDTO(
                external_id=str(m["id"]),
                sport=_SPORT,
                home_score=int(hs),
                away_score=int(as_),
                status=m.get("status", "FINISHED"),
            ))
        return out

    def fetch_season_fixtures(self, season: str) -> list[FixtureDTO]:
        """season : "2024" pour la saison 2024-25."""
        data = self._get(
            f"/competitions/{_COMPETITION}/matches",
            params={"season": season},
        )
        return [self._parse_match(m) for m in data.get("matches", [])]
