"""Client Open-Meteo — données météo pour le jour du match.

API gratuite, sans clé. Utilise le endpoint forecast pour les dates futures
et archive pour les dates passées.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import structlog

log = structlog.get_logger()

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL  = "https://archive-api.open-meteo.com/v1/archive"
_TIMEOUT      = 10


def fetch_weather(lat: float, lon: float, match_dt: datetime) -> dict[str, float]:
    """Retourne {temp_celsius, rain_mm, wind_kmh} pour l'heure du match.

    Utilise le endpoint archive pour les matchs passés, forecast pour les futurs.
    Retourne des valeurs neutres (0.0) en cas d'erreur.
    """
    match_date = match_dt.date() if hasattr(match_dt, "date") else match_dt
    today = date.today()

    url = _ARCHIVE_URL if match_date < today else _FORECAST_URL

    try:
        resp = httpx.get(
            url,
            params={
                "latitude":  lat,
                "longitude": lon,
                "hourly":    "temperature_2m,rain,windspeed_10m",
                "start_date": match_date.isoformat(),
                "end_date":   match_date.isoformat(),
                "timezone":  "UTC",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        hours = data["hourly"]["time"]
        target_h = match_dt.hour if hasattr(match_dt, "hour") else 15
        idx = min(range(len(hours)), key=lambda i: abs(_parse_hour(hours[i]) - target_h))

        return {
            "temp_celsius": float(data["hourly"]["temperature_2m"][idx] or 0.0),
            "rain_mm":      float(data["hourly"]["rain"][idx] or 0.0),
            "wind_kmh":     float(data["hourly"]["windspeed_10m"][idx] or 0.0),
        }

    except Exception as exc:
        log.warning("weather.fetch_failed", lat=lat, lon=lon, error=str(exc))
        return {"temp_celsius": 15.0, "rain_mm": 0.0, "wind_kmh": 0.0}


def _parse_hour(time_str: str) -> int:
    """Extrait l'heure d'une chaîne ISO-8601 comme '2025-03-15T18:00'."""
    try:
        return int(time_str.split("T")[1].split(":")[0])
    except (IndexError, ValueError):
        return 0
