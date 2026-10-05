"""Provider Équipe de France.

Chaîne de fallback (sans payer) :
  1. API-Football (100 req/j) — optionnel, si APIFOOTBALL_API_KEY est défini
  2. football-data.org (même clé que Ligue1, pas de quota journalier)
     → couvre Nations League, Coupe du Monde, Euro
     ⚠ Nations League (UNL) non couverte sur le tier gratuit (403)
  3. ESPN API non-officielle (gratuite, sans clé)
     → couvre Nations League, Qualifs CM/Euro, matchs amicaux
  4. TheSportsDB (gratuit, sans clé) — données en retard / endpoints 404

external_id : 'apf_{id}' | 'fdo_{id}' | 'espn_{id}' | 'tsdb_{id}'
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Optional

import httpx
import structlog

from .api_football import ApiFootballClient, FRANCE_TEAM_ID
from .base import DataProvider, FixtureDTO, ResultDTO
from .thesportsdb import TheSportsDBClient

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


class FranceNTFDOProvider:
    """Résultats France NT via football-data.org — même clé que Ligue1, aucun quota journalier.

    Couvre Nations League (UNL), Coupe du Monde (WC), Euro (EC).
    Les compétitions absentes du tier gratuit retournent 403 et sont silencieusement ignorées.
    """

    _BASE_URL = "https://api.football-data.org/v4"
    _RATE_LIMIT = 6.5  # 10 req/min

    _COMPETITIONS: dict[str, str] = {
        "UNL": "UEFA_UNL",
        "WC":  "FIFA_WC",
        "EC":  "UEFA_EC",
    }

    def __init__(self, api_key: str) -> None:
        self._headers = {"X-Auth-Token": api_key}
        self._last_req: float = 0.0

    def _get(self, path: str, params: dict) -> list[dict]:
        elapsed = time.monotonic() - self._last_req
        if elapsed < self._RATE_LIMIT:
            time.sleep(self._RATE_LIMIT - elapsed)
        try:
            resp = httpx.get(
                f"{self._BASE_URL}{path}",
                headers=self._headers,
                params=params,
                timeout=30,
            )
            self._last_req = time.monotonic()
            if resp.status_code == 403:
                log.warning("fdo_france_nt.comp_forbidden", path=path,
                            hint="Competition hors du tier gratuit football-data.org")
                return []
            resp.raise_for_status()
            matches = resp.json().get("matches", [])
            log.debug("fdo_france_nt.raw", path=path, params=params, n_total=len(matches))
            return matches
        except Exception as exc:
            log.warning("fdo_france_nt.request_failed", path=path, error=str(exc))
            return []

    @staticmethod
    def _is_france(m: dict) -> bool:
        return (
            m.get("homeTeam", {}).get("name") == "France"
            or m.get("awayTeam", {}).get("name") == "France"
        )

    def _to_dto(self, m: dict, comp_code: str) -> Optional[FixtureDTO]:
        try:
            score = m.get("score", {}).get("fullTime", {})
            hs = score.get("home")
            as_ = score.get("away")
            status = m.get("status", "SCHEDULED")
            if status == "FINISHED" and (hs is None or as_ is None):
                return None
            dt = _parse_date(m.get("utcDate") or "")
            return FixtureDTO(
                external_id=f"fdo_{m['id']}",
                sport=_SPORT,
                home_team_id=str(m["homeTeam"]["id"]),
                home_team_name=m["homeTeam"]["name"],
                away_team_id=str(m["awayTeam"]["id"]),
                away_team_name=m["awayTeam"]["name"],
                match_date=dt,
                season=str(dt.year),
                competition=self._COMPETITIONS.get(comp_code, "FRIENDLY"),
                status=status,
                home_score=int(hs) if hs is not None else None,
                away_score=int(as_) if as_ is not None else None,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def fetch_upcoming_fixtures(self, from_date: date, to_date: date) -> list[FixtureDTO]:
        out: list[FixtureDTO] = []
        for comp in self._COMPETITIONS:
            matches = self._get(
                f"/competitions/{comp}/matches",
                {"status": "SCHEDULED,TIMED", "dateFrom": from_date.isoformat(), "dateTo": to_date.isoformat()},
            )
            for m in matches:
                if not self._is_france(m):
                    continue
                dto = self._to_dto(m, comp)
                if dto:
                    out.append(dto)
        log.info("fdo_france_nt.upcoming_fetched", n=len(out))
        return out

    def fetch_recent_results(self, from_date: date, to_date: date) -> list[ResultDTO]:
        out: list[ResultDTO] = []
        for comp in self._COMPETITIONS:
            matches = self._get(
                f"/competitions/{comp}/matches",
                {"status": "FINISHED", "dateFrom": from_date.isoformat(), "dateTo": to_date.isoformat()},
            )
            for m in matches:
                if not self._is_france(m):
                    continue
                dto = self._to_dto(m, comp)
                if dto and dto.home_score is not None and dto.away_score is not None:
                    out.append(ResultDTO(
                        external_id=dto.external_id,
                        sport=_SPORT,
                        home_score=dto.home_score,
                        away_score=dto.away_score,
                        status="FINISHED",
                        home_team_name=dto.home_team_name,
                        away_team_name=dto.away_team_name,
                        match_date=dto.match_date,
                    ))
        log.info("fdo_france_nt.results_fetched", n=len(out))
        return out

    def fetch_season_fixtures(self, season: str) -> list[FixtureDTO]:
        out: list[FixtureDTO] = []
        for comp in self._COMPETITIONS:
            matches = self._get(f"/competitions/{comp}/matches", {"season": season})
            for m in matches:
                if not self._is_france(m):
                    continue
                dto = self._to_dto(m, comp)
                if dto:
                    out.append(dto)
        return out


class FranceNTESPNProvider:
    """ESPN API non-officielle — gratuite, sans clé.

    Couvre Nations League, Qualifs CM/Euro, Euro, Coupe du Monde et amicaux.
    Les slugs non reconnus retournent 404 et sont ignorés silencieusement.
    """

    _BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

    # (slug ESPN, code compétition interne)
    _LEAGUES: list[tuple[str, str]] = [
        ("uefa.nations",          "UEFA_UNL"),
        ("fifa.worldq.europe",    "FIFA_WCQ"),
        ("uefa.euro_qualifying",  "UEFA_ECQ"),
        ("uefa.euro",             "UEFA_EC"),
        ("fifa.world",            "FIFA_WC"),
        ("intl.friendlies.m",     "FRIENDLY"),
    ]

    def _get_events(self, slug: str, from_date: date, to_date: date) -> list[dict]:
        dates_param = f"{from_date.strftime('%Y%m%d')}-{to_date.strftime('%Y%m%d')}"
        try:
            resp = httpx.get(
                f"{self._BASE}/{slug}/scoreboard",
                params={"dates": dates_param, "limit": 100},
                timeout=15,
            )
            if resp.status_code in (404, 400):
                return []
            if resp.status_code == 403:
                log.debug("espn_france_nt.forbidden", slug=slug)
                return []
            resp.raise_for_status()
            return resp.json().get("events", [])
        except Exception as exc:
            log.warning("espn_france_nt.request_failed", slug=slug, error=str(exc))
            return []

    @staticmethod
    def _france_names(event: dict) -> tuple[str, str] | None:
        """Retourne (home_name, away_name) si France participe, sinon None."""
        for comp in event.get("competitions", []):
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue
            home = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away = next((c for c in competitors if c.get("homeAway") == "away"), None)
            if not home or not away:
                continue
            h_name = home.get("team", {}).get("displayName", "")
            a_name = away.get("team", {}).get("displayName", "")
            if "France" in h_name or "France" in a_name:
                return h_name, a_name
        return None

    def _event_to_fixture_dto(self, event: dict, comp_code: str) -> Optional[FixtureDTO]:
        try:
            names = self._france_names(event)
            if not names:
                return None
            home_name, away_name = names
            comp = event["competitions"][0]
            competitors = comp["competitors"]
            home = next(c for c in competitors if c.get("homeAway") == "home")
            away = next(c for c in competitors if c.get("homeAway") == "away")

            completed = comp.get("status", {}).get("type", {}).get("completed", False)
            status = "FINISHED" if completed else "SCHEDULED"
            home_score: Optional[int] = None
            away_score: Optional[int] = None
            if completed:
                hs = home.get("score")
                as_ = away.get("score")
                if hs is None or as_ is None:
                    return None
                home_score = int(hs)
                away_score = int(as_)

            dt = _parse_date(event.get("date", ""))
            home_id = home.get("team", {}).get("id", home_name)
            away_id = away.get("team", {}).get("id", away_name)

            return FixtureDTO(
                external_id=f"espn_{event['id']}",
                sport=_SPORT,
                home_team_id=str(home_id),
                home_team_name=home_name,
                away_team_id=str(away_id),
                away_team_name=away_name,
                match_date=dt,
                season=str(dt.year),
                competition=comp_code,
                status=status,
                home_score=home_score,
                away_score=away_score,
            )
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            log.debug("espn_france_nt.parse_error", error=str(exc))
            return None

    def fetch_upcoming_fixtures(self, from_date: date, to_date: date) -> list[FixtureDTO]:
        out: list[FixtureDTO] = []
        seen: set[str] = set()
        for slug, comp_code in self._LEAGUES:
            for event in self._get_events(slug, from_date, to_date):
                dto = self._event_to_fixture_dto(event, comp_code)
                if dto and dto.status != "FINISHED" and dto.external_id not in seen:
                    out.append(dto)
                    seen.add(dto.external_id)
        log.info("espn_france_nt.upcoming_fetched", n=len(out))
        return out

    def fetch_recent_results(self, from_date: date, to_date: date) -> list[ResultDTO]:
        out: list[ResultDTO] = []
        seen: set[str] = set()
        for slug, comp_code in self._LEAGUES:
            for event in self._get_events(slug, from_date, to_date):
                dto = self._event_to_fixture_dto(event, comp_code)
                if (
                    dto
                    and dto.status == "FINISHED"
                    and dto.home_score is not None
                    and dto.away_score is not None
                    and dto.external_id not in seen
                ):
                    out.append(ResultDTO(
                        external_id=dto.external_id,
                        sport=_SPORT,
                        home_score=dto.home_score,
                        away_score=dto.away_score,
                        status="FINISHED",
                        home_team_name=dto.home_team_name,
                        away_team_name=dto.away_team_name,
                        match_date=dto.match_date,
                    ))
                    seen.add(dto.external_id)
        log.info("espn_france_nt.results_fetched", n=len(out))
        return out

    def fetch_season_fixtures(self, season: str) -> list[FixtureDTO]:
        from_date = date(int(season), 1, 1)
        to_date   = date(int(season), 12, 31)
        out: list[FixtureDTO] = []
        seen: set[str] = set()
        for slug, comp_code in self._LEAGUES:
            for event in self._get_events(slug, from_date, to_date):
                dto = self._event_to_fixture_dto(event, comp_code)
                if dto and dto.external_id not in seen:
                    out.append(dto)
                    seen.add(dto.external_id)
        log.info("espn_france_nt.season_fetched", season=season, n=len(out))
        return out


class FranceNTProvider(DataProvider):
    def __init__(self, api_key: str, fdo_api_key: str = "") -> None:
        self._client = ApiFootballClient(api_key) if api_key else None
        self._fdo = FranceNTFDOProvider(fdo_api_key) if fdo_api_key else None
        self._espn = FranceNTESPNProvider()
        self._tsdb = TheSportsDBClient()
        self._apf_available: bool | None = None
        self._tsdb_past_cache: list[FixtureDTO] | None = None

    def _test_apf_key(self) -> bool:
        """Vérifie que la clé API-Football est valide (résultat mis en cache)."""
        if self._client is None:
            return False
        if self._apf_available is not None:
            return self._apf_available
        try:
            import httpx
            resp = httpx.get(
                "https://v3.football.api-sports.io/status",
                headers={"x-apisports-key": self._client._headers["x-apisports-key"]},
                timeout=10,
            )
            data = resp.json()
            self._apf_available = "errors" not in data or not data["errors"]
            if not self._apf_available:
                log.warning(
                    "france_nt.apf_key_invalid",
                    reason="Clé API-Football invalide ou quota épuisé — fallback TheSportsDB activé",
                )
        except Exception as exc:
            log.warning("france_nt.apf_check_failed", error=str(exc))
            self._apf_available = False
        return self._apf_available

    def fetch_upcoming_fixtures(self, from_date: date, to_date: date) -> list[FixtureDTO]:
        # ── Tentative API-Football ────────────────────────────────────────────
        if self._test_apf_key():
            current_year = date.today().year
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
            if out:
                log.info("france_nt.upcoming_fetched", source="api_football", n=len(out))
                return out

        # ── Fallback football-data.org ────────────────────────────────────────
        if self._fdo:
            out = self._fdo.fetch_upcoming_fixtures(from_date, to_date)
            if out:
                log.info("france_nt.upcoming_fetched", source="football_data", n=len(out))
                return out

        # ── Fallback ESPN (non-officiel, gratuit) ─────────────────────────────
        out = self._espn.fetch_upcoming_fixtures(from_date, to_date)
        if out:
            log.info("france_nt.upcoming_fetched", source="espn", n=len(out))
            return out

        # ── Fallback TheSportsDB ──────────────────────────────────────────────
        log.info("france_nt.upcoming_fallback", source="thesportsdb")
        tsdb_fixtures = self._tsdb.get_next_fixtures()
        out = [
            fx for fx in tsdb_fixtures
            if from_date <= fx.match_date.date() <= to_date
        ]
        log.info("france_nt.upcoming_fetched", source="thesportsdb", n=len(out))
        return out

    def _tsdb_past(self) -> list[FixtureDTO]:
        """Charge une fois l'historique TheSportsDB (eventspast + Nations League)."""
        if self._tsdb_past_cache is None:
            self._tsdb_past_cache = self._tsdb.get_france_all_past()
        return self._tsdb_past_cache

    def fetch_recent_results(self, from_date: date, to_date: date) -> list[ResultDTO]:
        # ── Tentative API-Football ────────────────────────────────────────────
        if self._test_apf_key():
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
                        home_team_name=dto.home_team_name,
                        away_team_name=dto.away_team_name,
                        match_date=dto.match_date,
                    ))
            if out:
                log.info("france_nt.results_fetched", source="api_football", n=len(out))
                return out

        # ── Fallback football-data.org ────────────────────────────────────────
        if self._fdo:
            out = self._fdo.fetch_recent_results(from_date, to_date)
            if out:
                log.info("france_nt.results_fetched", source="football_data", n=len(out))
                return out

        # ── Fallback ESPN (non-officiel, gratuit) ─────────────────────────────
        out = self._espn.fetch_recent_results(from_date, to_date)
        if out:
            log.info("france_nt.results_fetched", source="espn", n=len(out))
            return out

        # ── Fallback TheSportsDB ──────────────────────────────────────────────
        out = []
        for dto in self._tsdb_past():
            if dto.status != "FINISHED":
                continue
            if from_date <= dto.match_date.date() <= to_date:
                out.append(ResultDTO(
                    external_id=dto.external_id,
                    sport=_SPORT,
                    home_score=dto.home_score,  # type: ignore[arg-type]
                    away_score=dto.away_score,  # type: ignore[arg-type]
                    status="FINISHED",
                    home_team_name=dto.home_team_name,
                    away_team_name=dto.away_team_name,
                    match_date=dto.match_date,
                ))
        log.info("france_nt.results_fetched", source="thesportsdb", n=len(out))
        return out

    def fetch_season_fixtures(self, season: str) -> list[FixtureDTO]:
        """season = année calendaire (ex: '2024')."""
        # ── Tentative API-Football ────────────────────────────────────────────
        if self._test_apf_key():
            raw = self._client.get_france_fixtures(int(season))
            out = [dto for m in raw if (dto := _to_fixture_dto(m)) is not None]
            if out:
                log.info("france_nt.season_fetched", source="api_football", season=season, n=len(out))
                return out

        # ── Fallback football-data.org ────────────────────────────────────────
        if self._fdo:
            out = self._fdo.fetch_season_fixtures(season)
            if out:
                log.info("france_nt.season_fetched", source="football_data", season=season, n=len(out))
                return out

        # ── Fallback ESPN (non-officiel, gratuit) ─────────────────────────────
        out = self._espn.fetch_season_fixtures(season)
        if out:
            log.info("france_nt.season_fetched", source="espn", season=season, n=len(out))
            return out

        # ── Fallback TheSportsDB ──────────────────────────────────────────────
        out = [dto for dto in self._tsdb_past() if dto.season == season]
        log.info("france_nt.season_fetched", source="thesportsdb", season=season, n=len(out))
        return out

    def fetch_lineups(self, external_id: str) -> list[dict]:
        """Retourne les données brutes de compositions depuis API-Football."""
        apf_id = extract_apf_id(external_id)
        if apf_id is None:
            log.warning("france_nt.invalid_external_id", external_id=external_id)
            return []
        return self._client.get_fixture_lineups(apf_id)
