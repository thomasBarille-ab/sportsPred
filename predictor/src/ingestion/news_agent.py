"""Agent Claude avec web_search pour extraire les signaux pré-match.

Interroge le web pour trouver :
- Blessures et suspensions confirmées
- Signaux de rotation / équipe bis du coach
Et retourne un dict structuré.
"""

from __future__ import annotations

import json
from datetime import datetime

import structlog

log = structlog.get_logger()

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 512
_MAX_TURNS = 4

_SYSTEM = """\
Tu es un analyste football spécialisé dans l'analyse pré-match.
Réponds UNIQUEMENT en JSON valide, sans texte autour.
"""


def search_team_context(
    api_key: str,
    team_name: str,
    opponent_name: str,
    match_date: datetime,
    sport: str,
) -> dict:
    """Cherche sur le web les infos de disponibilité pour une équipe avant un match.

    Retourne {
        "absent_count": int,       — joueurs absents confirmés (blessés + suspendus)
        "rotation_signal": bool,   — coach annonce rotation / équipe remaniée
        "summary": str,            — résumé court (pour llm_analysis JSONB)
    }
    En cas d'erreur ou d'API key manquante : valeurs neutres.
    """
    if not api_key:
        return _neutral(team_name)

    try:
        import anthropic
    except ImportError:
        log.warning("news_agent.anthropic_not_installed")
        return _neutral(team_name)

    date_str = match_date.strftime("%d/%m/%Y")
    sport_label = "football" if sport in ("ligue1", "france_nt") else sport
    query = (
        f"blessures absences suspensions {team_name} avant match {opponent_name} {date_str} {sport_label}"
    )

    prompt = (
        f"Recherche les informations de disponibilité pour l'équipe '{team_name}' "
        f"avant leur match contre '{opponent_name}' le {date_str}.\n\n"
        f"Cherche : blessés, suspendus, joueurs incertains, signaux de rotation du coach.\n\n"
        f"Réponds UNIQUEMENT avec ce JSON (pas de markdown) :\n"
        f'{{"absent_count": <int>, "rotation_signal": <true|false>, "summary": "<1 phrase>"}}'
    )

    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]

    try:
        for _ in range(_MAX_TURNS):
            response = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text") and block.text.strip():
                        return _parse_response(block.text.strip(), team_name)
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        # web_search est géré nativement par Anthropic — le résultat
                        # est injecté automatiquement dans la réponse suivante.
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "",
                        })
                if tool_results:
                    messages.append({"role": "user", "content": tool_results})
                continue

            break

    except Exception as exc:
        log.warning("news_agent.search_failed", team=team_name, error=str(exc))

    return _neutral(team_name)


def _parse_response(text: str, team_name: str) -> dict:
    # Extrait le JSON même s'il y a du texte autour
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start == -1 or end == 0:
        return _neutral(team_name)
    try:
        data = json.loads(text[start:end])
        return {
            "absent_count":     int(data.get("absent_count", 0)),
            "rotation_signal":  bool(data.get("rotation_signal", False)),
            "summary":          str(data.get("summary", "")),
        }
    except (json.JSONDecodeError, ValueError):
        return _neutral(team_name)


def _neutral(team_name: str) -> dict:
    return {"absent_count": 0, "rotation_signal": False, "summary": ""}
