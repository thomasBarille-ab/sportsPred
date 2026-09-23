"""Ingestion des cotes historiques Ligue 1 depuis football-data.co.uk.

CSV gratuit, sans clé API.
URL pattern : https://www.football-data.co.uk/mmz4281/{YY}{YY+1}/F1.csv

Colonnes utilisées (par ordre de préférence) :
  PSCH / PSCD / PSCA : Pinnacle closing (cotes les plus fiables)
  B365H / B365D / B365A : Bet365
  BWH  / BWD  / BWA  : Betway
  MaxH / MaxD / MaxA : Maximum bookmaker
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

import httpx
import structlog

log = structlog.get_logger()

_BASE_URL = "https://www.football-data.co.uk/mmz4281"

# Ordre de préférence bookmaker (columns CSV)
_BOOKMAKER_COLS = [
    ("PSCH", "PSCD", "PSCA", "pinnacle"),
    ("B365H", "B365D", "B365A", "bet365"),
    ("BWH", "BWD", "BWA", "betway"),
    ("MaxH", "MaxD", "MaxA", "max"),
]


def _season_code(start_year: int) -> str:
    """Ex: 2024 → '2425' pour la saison 2024-25."""
    return f"{str(start_year)[2:]}{str(start_year + 1)[2:]}"


def _parse_date(s: str) -> date | None:
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _best_odds(row: dict) -> tuple[float, float, float, str] | None:
    """Retourne (odds_home, odds_draw, odds_away, bookmaker) ou None."""
    for h_col, d_col, a_col, bm in _BOOKMAKER_COLS:
        try:
            h = float(row[h_col])
            d = float(row[d_col])
            a = float(row[a_col])
            if h > 1.0 and d > 1.0 and a > 1.0:
                return h, d, a, bm
        except (KeyError, ValueError):
            continue
    return None


def fetch_season_ligue1_odds(start_year: int) -> list[dict]:
    """Télécharge et parse les cotes Ligue 1 pour une saison.

    Retourne une liste de dicts :
    {date, home_team, away_team, odds_home, odds_draw, odds_away, bookmaker}
    """
    code = _season_code(start_year)
    url = f"{_BASE_URL}/{code}/F1.csv"
    log.info("football_data_odds.download", url=url, season=f"{start_year}-{start_year + 1}")

    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            log.warning("football_data_odds.not_found", url=url,
                        hint="Saison peut-être pas encore disponible sur le site")
            return []
        raise

    # football-data.co.uk utilise souvent latin-1
    try:
        text = resp.content.decode("utf-8")
    except UnicodeDecodeError:
        text = resp.content.decode("latin-1")

    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        home = raw.get("HomeTeam", "").strip()
        away = raw.get("AwayTeam", "").strip()
        date_str = raw.get("Date", "").strip()
        if not home or not away or not date_str:
            continue

        match_date = _parse_date(date_str)
        if match_date is None:
            continue

        best = _best_odds(raw)
        if best is None:
            continue

        odds_home, odds_draw, odds_away, bookmaker = best
        rows.append({
            "date": match_date,
            "home_team": home,
            "away_team": away,
            "odds_home": odds_home,
            "odds_draw": odds_draw,
            "odds_away": odds_away,
            "bookmaker": bookmaker,
        })

    log.info("football_data_odds.parsed", season=code, rows=len(rows))
    return rows
