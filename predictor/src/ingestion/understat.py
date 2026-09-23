"""Scraper Understat — xG (expected goals) pour la Ligue 1.

Understat n'a pas d'API publique. Les données sont embarquées en JSON
dans une balise <script> de la page HTML de chaque ligue.

URL : https://understat.com/league/Ligue_1/{start_year}
Données extraites : home_xg, away_xg par match joué.

Matching vers nos fixtures : date (±2h) + noms d'équipes (fuzzy).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher

import httpx
import structlog

log = structlog.get_logger()

_BASE_URL = "https://understat.com/league/Ligue_1"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


@dataclass
class XGRow:
    home_team: str
    away_team: str
    match_datetime: datetime
    home_xg: float
    away_xg: float


def _norm(name: str) -> str:
    s = name.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"\b(fc|sc|as|og|ogc|rc|aj|stade|olympique|de|du|le|la)\b", " ", s)
    return " ".join(s.split())


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _extract_dates_data(html: str) -> list[dict] | None:
    """Extrait le tableau datesData depuis le HTML de la page Understat."""
    # La donnée est encodée comme : var datesData = JSON.parse('...')
    match = re.search(r"var\s+datesData\s*=\s*JSON\.parse\('(.+?)'\)\s*;", html, re.DOTALL)
    if not match:
        return None
    raw = match.group(1)
    # Décode les séquences d'échappement unicode (\uXXXX) et les backslashes
    try:
        decoded = raw.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        decoded = raw.encode("utf-8").decode("unicode_escape")
    try:
        return json.loads(decoded)
    except json.JSONDecodeError:
        return None


def fetch_season_xg(start_year: int) -> list[XGRow]:
    """Retourne les xG de tous les matchs terminés pour une saison Ligue 1."""
    url = f"{_BASE_URL}/{start_year}"
    log.info("understat.fetch", url=url, season=f"{start_year}-{start_year + 1}")

    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.error("understat.http_error", status=exc.response.status_code, url=url)
        return []
    except Exception as exc:
        log.error("understat.error", error=str(exc))
        return []

    data = _extract_dates_data(resp.text)
    if data is None:
        log.warning("understat.parse_failed", url=url, hint="Structure HTML peut avoir changé")
        return []

    rows: list[XGRow] = []
    for m in data:
        if not m.get("isResult"):
            continue
        try:
            home_xg = float(m["xG"]["h"])
            away_xg = float(m["xG"]["a"])
            home_team = m["h"]["title"]
            away_team = m["a"]["title"]
            dt_str = m["datetime"]  # "2024-08-17 21:00:00"
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        rows.append(XGRow(home_team=home_team, away_team=away_team,
                          match_datetime=dt, home_xg=home_xg, away_xg=away_xg))

    log.info("understat.parsed", season=start_year, matches=len(rows))
    return rows


def match_xg_to_fixtures(
    xg_rows: list[XGRow],
    fixtures: list[dict],
    window_hours: int = 6,
) -> dict[int, tuple[float, float]]:
    """Associe les xG aux fixture_ids de notre DB.

    Retourne {fixture_id: (home_xg, away_xg)}.
    """
    result: dict[int, tuple[float, float]] = {}

    for row in xg_rows:
        best_id: int | None = None
        best_score = 0.0

        for f in fixtures:
            mt = f["match_date"]
            if hasattr(mt, "tzinfo") and mt.tzinfo is None:
                mt = mt.replace(tzinfo=timezone.utc)
            if abs((mt - row.match_datetime).total_seconds()) > window_hours * 3600:
                continue
            score = _sim(row.home_team, f["home_team_name"]) + _sim(row.away_team, f["away_team_name"])
            if score > best_score:
                best_score = score
                best_id = f["id"]

        if best_id and best_score >= 0.8:
            result[best_id] = (row.home_xg, row.away_xg)

    return result
