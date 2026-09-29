"""Provider Équipe de France — implémente DataProvider via API-Football.

France national team ID (API-Football) : 2.
external_id format : 'apf_{fixture_id}'.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

import structlog

from .api_football import ApiFootballClient, FRANCE_TEAM_ID
from .base import DataProvider, FixtureDTO, ResultDTO

log = structlog.get_logger()

_SPORT = "france_nt"

_FINISHED_STATUSES = {"FT", "AET", "PEN"}
_UPCOMING_STATUSES = {"NS", "TBD"}
_CANCELLED_STATUSES = {"CANC", "ABD", "SUSP", "PST", "WO", "AWD"}


def _parse_date(s: str) -> datetime:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _to_fixture_dto(match: dict) -> Optional[FixtureDTO]:
    try:
        fx = match["fixture"]
        league = match["league"]
        teams = match["teams"]
        goals = match.get("goals", {})

        league_name = league.get("name", "")
        if not ApiFootballClient.is_relevant(league_name):
            return None

        competition_code = ApiFootballClient.map_competition_code(league_name)

        status_short = fx.get("status", {}).get("short", "NS")
        if status_short in _FINISHED_STATUSES:
            status = "FINISHED"
        elif status_short in _CANCELLED_STATUSES:
            status = "POSTPONED"
        else:
            status = "SCHEDULED"

        home_score: Optional[int] = None
        away_score: Optional[int] = None
        if status == "FINISHED":
            gs_h = goals.get("home")
            gs_a = goals.get("away")
            if gs_h is None or gs_a is None:
                return None
            home_score = int(gs_h)
            away_score = int(gs_a)

        match_dt = _parse_date(fx.get("date", ""))

        return FixtureDTO(
            external_id=f"apf_{fx['id']}",
            sport=_SPORT,
            home_team_id=str(teams["home"]["id"]),
            home_team_name=teams["home"]["name"],
            away_team_id=str(teams["away"]["id"]),
            away_team_name=teams["away"]["name"],
            match_date=match_dt,
            season=str(match_dt.year),
            competition=competition_code,
            status=status,
            home_score=home_score,
            away_score=away_score,
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("france_nt.parse_error", error=str(exc))
        return None


def extract_apf_id(external_id: str) -> Optional[int]:
    """Extrait l'ID numérique API-Football depuis 'apf_12345'."""
    try:
        return int(external_id.replace("apf_", ""))
    except ValueError:
        return None


class FranceNTProvider(DataProvider):
    def __init__(self, api_key: str) -> None:
        self._client = ApiFootballClient(api_key)

    def fetch_upcoming_fixtures(self, from_date: date, to_date: date) -> list[FixtureDTO]:
        current_year = date.today().year
        # Couvre aussi l'année suivante pour les matchs programmés longtemps à l'avance
        raw: list[dict] = []
        for yr in {current_year, current_year + 1}:
            raw.extend(self._client.get_france_fixtures(yr))

        out = []
        for match in raw:
            dto = _to_fixture_dto(match)
            if dto is None or dto.status == "FINISHED":
                continue
            if from_date <= dto.match_date.date() <= to_date:
                out.append(dto)
        log.info("france_nt.upcoming_fetched", n=len(out))
        return out

    def fetch_recent_results(self, from_date: date, to_date: date) -> list[ResultDTO]:
        current_year = date.today().year
        raw = self._client.get_france_fixtures(current_year)

        out = []
        for match in raw:
            dto = _to_fixture_dto(match)
            if dto is None or dto.status != "FINISHED":
                continue
            if from_date <= dto.match_date.date() <= to_date:
                out.append(ResultDTO(
                    external_id=dto.external_id,
                    sport=_SPORT,
                    home_score=dto.home_score,  # type: ignore[arg-type]
                    away_score=dto.away_score,  # type: ignore[arg-type]
                    status="FINISHED",
                ))
        log.info("france_nt.results_fetched", n=len(out))
        return out

    def fetch_season_fixtures(self, season: str) -> list[FixtureDTO]:
        """season = année calendaire (ex: '2024')."""
        raw = self._client.get_france_fixtures(int(season))
        out = [dto for m in raw if (dto := _to_fixture_dto(m)) is not None]
        log.info("france_nt.season_fetched", season=season, n=len(out))
        return out

    def fetch_lineups(self, external_id: str) -> list[dict]:
        """Retourne les données brutes de compositions depuis API-Football."""
        apf_id = extract_apf_id(external_id)
        if apf_id is None:
            log.warning("france_nt.invalid_external_id", external_id=external_id)
            return []
        return self._client.get_fixture_lineups(apf_id)
