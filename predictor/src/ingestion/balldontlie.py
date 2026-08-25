"""Provider balldontlie.io — NBA.

Tier gratuit : 5 req/min. Un token est maintenant obligatoire même sur le tier gratuit.
Inscription gratuite sur https://app.balldontlie.io → Dashboard → API Key.
Mettre la clé dans BALLDONTLIE_API_KEY.
Doc : https://www.balldontlie.io/api/v1
"""

import time
from datetime import date, datetime, timezone
from typing import Any

import httpx
import structlog

from .base import DataProvider, FixtureDTO, ResultDTO

log = structlog.get_logger()

_BASE_URL = "https://api.balldontlie.io/v1"
_SPORT = "nba"
_RATE_LIMIT_DELAY = 13.0  # ~4.5 req/min pour rester sous la limite de 5


def _season_from_date(d: datetime) -> str:
    """NBA season: si le match est avant juillet, c'est la saison N-1."""
    return str(d.year - 1) if d.month < 7 else str(d.year)


class BallDontLieProvider(DataProvider):
    def __init__(self, api_key: str = "") -> None:
        self._headers = {"Authorization": api_key} if api_key else {}
        self._last_request_at: float = 0.0

    def _get(self, path: str, params: dict | None = None) -> Any:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < _RATE_LIMIT_DELAY:
            time.sleep(_RATE_LIMIT_DELAY - elapsed)

        url = f"{_BASE_URL}{path}"
        log.debug("balldontlie.request", url=url, params=params)
        resp = httpx.get(url, headers=self._headers, params=params, timeout=30)
        self._last_request_at = time.monotonic()

        if resp.status_code == 429:
            log.warning("balldontlie.rate_limited")
            time.sleep(61)
            return self._get(path, params)

        resp.raise_for_status()
        return resp.json()

    def _get_paginated(self, path: str, params: dict | None = None) -> list[dict]:
        params = dict(params or {})
        params.setdefault("per_page", 100)
        results: list[dict] = []
        cursor: str | None = None

        while True:
            if cursor:
                params["cursor"] = cursor
            data = self._get(path, params)
            results.extend(data.get("data", []))
            meta = data.get("meta", {})
            cursor = meta.get("next_cursor")
            if not cursor:
                break

        return results

    def _parse_game(self, g: dict) -> FixtureDTO:
        dt_str = g.get("date", "")
        # balldontlie renvoie "2024-12-25" (date uniquement) ou ISO
        try:
            if "T" in dt_str:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)

        home = g.get("home_team", {})
        away = g.get("visitor_team", {})
        home_score = g.get("home_team_score")
        away_score = g.get("visitor_team_score")
        status = "FINISHED" if (home_score is not None and home_score > 0) else "SCHEDULED"

        return FixtureDTO(
            external_id=str(g["id"]),
            sport=_SPORT,
            home_team_id=str(home.get("id", "")),
            home_team_name=home.get("full_name", home.get("name", "")),
            away_team_id=str(away.get("id", "")),
            away_team_name=away.get("full_name", away.get("name", "")),
            match_date=dt,
            season=str(g.get("season", _season_from_date(dt))),
            competition="NBA",
            status=status,
            home_score=int(home_score) if home_score else None,
            away_score=int(away_score) if away_score else None,
        )

    def _date_range_games(self, from_date: date, to_date: date, batch_size: int = 5) -> list[dict]:
        """Récupère les matchs d'une fenêtre de dates en groupant par batch.

        balldontlie accepte plusieurs `dates[]` dans une même requête.
        batch_size=5 → 22 jours = 5 requêtes au lieu de 22.
        """
        from datetime import timedelta
        all_games: list[dict] = []
        day = from_date
        while day <= to_date:
            batch = [
                (day + timedelta(days=i)).isoformat()
                for i in range(batch_size)
                if (day + timedelta(days=i)) <= to_date
            ]
            params: dict = {}
            for d in batch:
                params.setdefault("dates[]", [])
                params["dates[]"].append(d)
            games = self._get_paginated("/games", params)
            all_games.extend(games)
            day += timedelta(days=batch_size)
        return all_games

    def fetch_upcoming_fixtures(self, from_date: date, to_date: date) -> list[FixtureDTO]:
        games = self._date_range_games(from_date, to_date)
        parsed = [self._parse_game(g) for g in games]
        return [fx for fx in parsed if fx.status == "SCHEDULED"]

    def fetch_recent_results(self, from_date: date, to_date: date) -> list[ResultDTO]:
        games = self._date_range_games(from_date, to_date)
        results: list[ResultDTO] = []
        for g in games:
            hs = g.get("home_team_score")
            as_ = g.get("visitor_team_score")
            if hs is None or as_ is None or hs == 0:
                continue
            results.append(ResultDTO(
                    external_id=str(g["id"]),
                    sport=_SPORT,
                    home_score=int(hs),
                    away_score=int(as_),
                    status="FINISHED",
                ))
        return results

    def fetch_season_fixtures(self, season: str) -> list[FixtureDTO]:
        """season : "2024" pour la saison NBA 2024-25."""
        games = self._get_paginated("/games", {"seasons[]": [season]})
        return [self._parse_game(g) for g in games]
