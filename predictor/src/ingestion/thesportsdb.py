"""Client TheSportsDB v1 — calendrier Équipe de France NT.

API gratuite, sans clé. Limite : 1 prochain match via eventsnext,
et historique récent via eventspast.

TheSportsDB IDs :
  team  = 133913 (France national team)
  league = 4490  (UEFA Nations League)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

import httpx
import structlog

from .base import FixtureDTO

log = structlog.get_logger()

_BASE   = "https://www.thesportsdb.com/api/v1/json/3"
_TEAM   = 133913
_TIMEOUT = 15

# TheSportsDB league IDs for competitions France NT plays in
_LEAGUE_NATIONS_LEAGUE = 4490
_LEAGUES_FOR_BACKFILL = [_LEAGUE_NATIONS_LEAGUE]

_COMP_MAP: dict[str, str] = {
    "uefa nations league":              "UEFA_UNL",
    "nations league":                   "UEFA_UNL",
    "world cup - qualification europe": "FIFA_WCQ",
    "world cup qualification":          "FIFA_WCQ",
    "euro qualification":               "UEFA_ECQ",
    "european championship":            "UEFA_EC",
    "world cup":                        "FIFA_WC",
    "friendly":                         "FRIENDLY",
    "friendlies":                       "FRIENDLY",
    "international friendly":           "FRIENDLY",
}

_FINISHED_KEYWORDS = {"final", "finished", "ft", "aet", "pen"}


def _map_competition(name: str) -> str:
    lower = name.lower()
    for kw, code in _COMP_MAP.items():
        if kw in lower:
            return code
    return "FRIENDLY"


def _parse_dt(date_str: str, time_str: str) -> datetime:
    try:
        if time_str and time_str != "00:00:00":
            dt = datetime.fromisoformat(f"{date_str}T{time_str}")
        else:
            dt = datetime.fromisoformat(f"{date_str}T15:00:00")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _event_to_dto(e: dict, status: str) -> Optional[FixtureDTO]:
    try:
        home = e["strHomeTeam"]
        away = e["strAwayTeam"]
        if not home or not away:
            return None

        # Filtre compétitions non pertinentes (jeunes, femmes…)
        comp_name = e.get("strLeague", "")
        league_lower = comp_name.lower()
        if any(tag in league_lower for tag in ["u21", "u23", "women", "youth", "olympic", "espoirs"]):
            return None

        competition = _map_competition(comp_name)
        match_dt = _parse_dt(e.get("dateEvent", ""), e.get("strTime", ""))

        home_score: Optional[int] = None
        away_score: Optional[int] = None
        if status == "FINISHED":
            hs = e.get("intHomeScore")
            as_ = e.get("intAwayScore")
            if hs is not None and as_ is not None:
                home_score = int(hs)
                away_score = int(as_)

        event_id = e.get("idEvent", "")

        # Use canonical API-Football ID "2" for France so historical ELO carries over
        home_id = "2" if home == "France" else f"tsdb_{e.get('idHomeTeam', home)}"
        away_id = "2" if away == "France" else f"tsdb_{e.get('idAwayTeam', away)}"

        return FixtureDTO(
            external_id=f"tsdb_{event_id}",
            sport="france_nt",
            home_team_id=home_id,
            home_team_name=home,
            away_team_id=away_id,
            away_team_name=away,
            match_date=match_dt,
            season=str(match_dt.year),
            competition=competition,
            status=status,
            home_score=home_score,
            away_score=away_score,
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.debug("thesportsdb.parse_error", error=str(exc))
        return None


class TheSportsDBClient:
    """Client léger TheSportsDB pour les matchs de l'Équipe de France."""

    def get_next_fixtures(self) -> list[FixtureDTO]:
        """Retourne le(s) prochain(s) match(s) de l'Équipe de France."""
        try:
            resp = httpx.get(f"{_BASE}/eventsnext.php", params={"id": _TEAM}, timeout=_TIMEOUT)
            resp.raise_for_status()
            events = resp.json().get("events") or []
        except Exception as exc:
            log.warning("thesportsdb.next_failed", error=str(exc))
            return []

        out = []
        for e in events:
            dto = _event_to_dto(e, "SCHEDULED")
            if dto:
                out.append(dto)
        log.info("thesportsdb.next_fetched", n=len(out))
        return out

    def get_past_fixtures(self) -> list[FixtureDTO]:
        """Retourne les derniers matchs terminés de l'Équipe de France.

        Essaie eventslast5.php en premier (endpoint actif sur le tier gratuit),
        puis eventspast.php comme fallback legacy.
        """
        for endpoint in ("eventslast5.php", "eventspast.php"):
            try:
                resp = httpx.get(f"{_BASE}/{endpoint}", params={"id": _TEAM}, timeout=_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                events = data.get("results") or data.get("events") or []
                if events:
                    out = []
                    for e in events:
                        hs = e.get("intHomeScore")
                        as_ = e.get("intAwayScore")
                        status = "FINISHED" if (hs is not None and as_ is not None) else "SCHEDULED"
                        dto = _event_to_dto(e, status)
                        if dto:
                            out.append(dto)
                    log.info("thesportsdb.past_fetched", endpoint=endpoint, n=len(out))
                    return out
            except Exception as exc:
                log.warning("thesportsdb.past_failed", endpoint=endpoint, error=str(exc))

        return []

    def get_season_fixtures(self, league_id: int, season: str) -> list[FixtureDTO]:
        """Retourne tous les matchs d'une saison pour une ligue donnée, filtrés pour la France."""
        try:
            resp = httpx.get(
                f"{_BASE}/eventsseason.php",
                params={"id": league_id, "s": season},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            events = resp.json().get("events") or []
        except Exception as exc:
            log.warning("thesportsdb.season_failed", error=str(exc))
            return []

        out = []
        for e in events:
            home = e.get("strHomeTeam", "")
            away = e.get("strAwayTeam", "")
            if "France" not in home and "France" not in away:
                continue
            hs = e.get("intHomeScore")
            as_ = e.get("intAwayScore")
            status = "FINISHED" if (hs is not None and as_ is not None) else "SCHEDULED"
            dto = _event_to_dto(e, status)
            if dto:
                out.append(dto)
        log.info("thesportsdb.season_fetched", league_id=league_id, season=season, n=len(out))
        return out

    def get_france_all_past(self, seasons_back: int = 4) -> list[FixtureDTO]:
        """Agrège l'historique France NT : eventspast + Nations League sur `seasons_back` saisons.

        Fallback quand API-Football n'est pas disponible. Retourne des FixtureDTO
        dédupliqués par external_id. Utilisé pour le backfill.
        """
        current_year = date.today().year
        # TheSportsDB uses "YYYY-YYYY" season format
        seasons = [f"{y}-{y+1}" for y in range(current_year - seasons_back, current_year + 1)]

        aggregated: dict[str, FixtureDTO] = {}

        for fx in self.get_past_fixtures():
            aggregated[fx.external_id] = fx

        for league_id in _LEAGUES_FOR_BACKFILL:
            for s in seasons:
                for fx in self.get_season_fixtures(league_id, s):
                    aggregated.setdefault(fx.external_id, fx)

        out = list(aggregated.values())
        log.info("thesportsdb.france_all_past_fetched", n=len(out), seasons=len(seasons))
        return out
