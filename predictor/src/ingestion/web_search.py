"""Wrapper DuckDuckGo Search — recherche web sans clé API."""

from __future__ import annotations

import structlog

log = structlog.get_logger()

_MAX_RESULTS = 4


def _search(query: str) -> list[str]:
    try:
        from duckduckgo_search import DDGS
        results = list(DDGS().text(query, max_results=_MAX_RESULTS))
        snippets = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            if title or body:
                snippets.append(f"{title} — {body[:250]}")
        log.debug("web_search.ok", query=query[:60], n=len(snippets))
        return snippets
    except Exception as exc:
        log.warning("web_search.failed", query=query[:60], error=str(exc))
        return []


def search_match_context(
    home_team: str,
    away_team: str,
    sport: str,
) -> dict[str, list[str]]:
    """3 recherches ciblées pour un match. Retourne les snippets par thème."""
    sport_label = "football ligue 1" if sport == "ligue1" else "NBA basketball"

    queries = {
        "injuries": f"{home_team} {away_team} blessures forfait suspension {sport_label}",
        "preview": f"{home_team} vs {away_team} preview pronostic {sport_label}",
        "news": f"{home_team} {away_team} actualité {sport_label}",
    }

    return {theme: _search(q) for theme, q in queries.items()}
