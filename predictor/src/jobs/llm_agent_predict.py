"""Agent LLM de prédiction indépendant — tourne 1h avant chaque match Ligue 1 / NBA.

Utilise Ollama (qwen2.5:7b) + DuckDuckGo pour prédire sans modèle ML.
Les prédictions sont stockées dans llm_agent_predictions pour comparaison post-match.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from ..db import session
from ..ingestion.web_search import search_match_context

log = structlog.get_logger()

_SPORTS = ("ligue1", "nba")
_LOOKAHEAD_MIN = 5    # ne pas prédire un match déjà commencé
_LOOKAHEAD_MAX = 90   # fenêtre : matchs qui démarrent dans les 90 prochaines minutes


def _fetch_upcoming(now: datetime) -> list[dict]:
    """Fixtures ligue1/nba sans prédiction agent, démarrant dans 5–90 min."""
    horizon_min = now + timedelta(minutes=_LOOKAHEAD_MIN)
    horizon_max = now + timedelta(minutes=_LOOKAHEAD_MAX)
    return session.fetch_all(
        """
        SELECT f.id, f.sport, f.home_team_name, f.away_team_name, f.match_date
        FROM fixtures f
        WHERE f.sport = ANY(%s)
          AND f.status IN ('SCHEDULED', 'TIMED')
          AND f.match_date BETWEEN %s AND %s
          AND NOT EXISTS (
            SELECT 1 FROM llm_agent_predictions ap WHERE ap.fixture_id = f.id
          )
        ORDER BY f.match_date
        """,
        (_SPORTS, horizon_min, horizon_max),
    )


def _fetch_team_form(team: str, sport: str, n: int = 5) -> list[dict]:
    return session.fetch_all(
        """
        SELECT f.home_team_name, f.away_team_name,
               r.home_score, r.away_score, f.match_date
        FROM fixtures f
        JOIN results r ON r.fixture_id = f.id
        WHERE f.sport = %s
          AND (f.home_team_name = %s OR f.away_team_name = %s)
        ORDER BY f.match_date DESC
        LIMIT %s
        """,
        (sport, team, team, n),
    )


def _fetch_h2h(home: str, away: str, sport: str, n: int = 5) -> list[dict]:
    return session.fetch_all(
        """
        SELECT f.home_team_name, f.away_team_name,
               r.home_score, r.away_score, f.match_date
        FROM fixtures f
        JOIN results r ON r.fixture_id = f.id
        WHERE f.sport = %s
          AND ((f.home_team_name = %s AND f.away_team_name = %s)
            OR (f.home_team_name = %s AND f.away_team_name = %s))
        ORDER BY f.match_date DESC
        LIMIT %s
        """,
        (sport, home, away, away, home, n),
    )


def _fetch_absents(fixture_id: int) -> list[dict]:
    try:
        return session.fetch_all(
            "SELECT team_name, player_name, reason FROM absent_players WHERE fixture_id = %s",
            (fixture_id,),
        )
    except Exception:
        return []


def _form_str(rows: list[dict], team: str) -> str:
    if not rows:
        return "Aucune donnée"
    parts = []
    for r in rows:
        h, a = int(r["home_score"]), int(r["away_score"])
        if r["home_team_name"] == team:
            result = "V" if h > a else ("N" if h == a else "D")
            parts.append(f"{result} {h}-{a} vs {r['away_team_name']}")
        else:
            result = "V" if a > h else ("N" if h == a else "D")
            parts.append(f"{result} {a}-{h} vs {r['home_team_name']}")
    return " | ".join(parts)


def _h2h_str(rows: list[dict]) -> str:
    if not rows:
        return "Aucun historique"
    parts = []
    for r in rows:
        h, a = int(r["home_score"]), int(r["away_score"])
        parts.append(f"{r['home_team_name']} {h}-{a} {r['away_team_name']}")
    return " | ".join(parts)


def _build_prompt(fixture: dict, home_form: list, away_form: list,
                  h2h: list, absents: list, snippets: dict) -> str:
    sport = fixture["sport"]
    sport_label = "Ligue 1 (football)" if sport == "ligue1" else "NBA (basketball)"
    outcomes = '"home" ou "draw" ou "away"' if sport == "ligue1" else '"home" ou "away"'
    match_dt = fixture["match_date"]
    if isinstance(match_dt, datetime):
        date_str = match_dt.strftime("%d/%m/%Y %H:%M UTC")
    else:
        date_str = str(match_dt)

    home = fixture["home_team_name"]
    away = fixture["away_team_name"]

    absents_str = "Aucune absence connue"
    if absents:
        absents_str = " | ".join(
            f"{a['player_name']} ({a['team_name']}, {a['reason'] or 'blessure'})"
            for a in absents
        )

    def fmt_snippets(items: list[str]) -> str:
        return "\n".join(f"  - {s}" for s in items) if items else "  Aucun résultat"

    return f"""Tu es un analyste sportif expert. Analyse les informations suivantes et prédit le résultat du match.

=== MATCH ===
{sport_label} — {date_str}
{home} (DOMICILE) vs {away} (EXTÉRIEUR)

=== FORME RÉCENTE (5 derniers matchs) ===
{home} : {_form_str(home_form, home)}
{away} : {_form_str(away_form, away)}

=== CONFRONTATIONS DIRECTES (5 derniers) ===
{_h2h_str(h2h)}

=== ABSENCES CONNUES ===
{absents_str}

=== ACTUALITÉS (recherche web) ===
Blessures/suspensions :
{fmt_snippets(snippets.get('injuries', []))}
Preview/pronostic :
{fmt_snippets(snippets.get('preview', []))}
Actualités récentes :
{fmt_snippets(snippets.get('news', []))}

=== INSTRUCTION ===
Réponds UNIQUEMENT en JSON valide avec exactement ces 3 champs :
{{
  "predicted_outcome": {outcomes},
  "confidence": <entier entre 40 et 85>,
  "reasoning": "<2-3 phrases maximum expliquant ta prédiction>"
}}"""


def _call_ollama(url: str, model: str, prompt: str) -> dict:
    resp = httpx.post(
        f"{url}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 250},
        },
        timeout=120,
    )
    resp.raise_for_status()
    raw = resp.json().get("response", "").strip()
    return json.loads(raw)


def _validate_response(data: dict, sport: str) -> tuple[str, int, str]:
    outcome = str(data.get("predicted_outcome", "")).lower()
    valid = {"home", "away"} if sport == "nba" else {"home", "draw", "away"}
    if outcome not in valid:
        raise ValueError(f"predicted_outcome invalide : {outcome!r}")
    confidence = int(data.get("confidence", 50))
    confidence = max(1, min(99, confidence))
    reasoning = str(data.get("reasoning", "")).strip()
    if not reasoning:
        raise ValueError("reasoning vide")
    return outcome, confidence, reasoning


def run_llm_agent_predict(ollama_url: str, model: str) -> dict:
    now = datetime.now(UTC)
    fixtures = _fetch_upcoming(now)

    log.info("llm_agent_predict.start", fixtures=len(fixtures), model=model)

    n_ok = n_err = 0

    for fixture in fixtures:
        fid = fixture["id"]
        home = fixture["home_team_name"]
        away = fixture["away_team_name"]
        sport = fixture["sport"]

        try:
            home_form = _fetch_team_form(home, sport)
            away_form = _fetch_team_form(away, sport)
            h2h = _fetch_h2h(home, away, sport)
            absents = _fetch_absents(fid)

            log.info("llm_agent_predict.searching_web", home=home, away=away)
            snippets = search_match_context(home, away, sport)

            prompt = _build_prompt(fixture, home_form, away_form, h2h, absents, snippets)

            log.info("llm_agent_predict.calling_ollama", home=home, away=away, model=model)
            raw = _call_ollama(ollama_url, model, prompt)

            outcome, confidence, reasoning = _validate_response(raw, sport)

            session.execute(
                """
                INSERT INTO llm_agent_predictions
                  (fixture_id, sport, predicted_outcome, confidence, reasoning,
                   search_snippets, model_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fixture_id) DO NOTHING
                """,
                (fid, sport, outcome, confidence, reasoning,
                 json.dumps(snippets), model),
            )
            n_ok += 1
            log.info(
                "llm_agent_predict.done",
                match=f"{home} vs {away}",
                outcome=outcome,
                confidence=confidence,
            )

        except Exception as exc:
            n_err += 1
            log.error(
                "llm_agent_predict.error",
                match=f"{home} vs {away}",
                error=str(exc),
            )

    log.info("llm_agent_predict.summary", ok=n_ok, errors=n_err)
    return {"predicted": n_ok, "errors": n_err}
