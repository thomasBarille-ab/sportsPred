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

from ..llm_client import create_message

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

    prompt = (
        f"Recherche les informations de disponibilité pour l'équipe '{team_name}' "
        f"avant leur match contre '{opponent_name}' le {date_str}.\n\n"
        f"Cherche : blessés, suspendus, joueurs incertains, signaux de rotation du coach.\n\n"
        f"Réponds UNIQUEMENT avec ce JSON (pas de markdown) :\n"
        f'{{"absent_count": <int>, '
        f'"absent_players": [{{"name": "<nom>", "reason": "<blessure|suspension|incertain>"}}], '
        f'"rotation_signal": <true|false>, '
        f'"summary": "<1 phrase>", '
        f'"sources": ["<url ou titre source>"]}}'
    )

    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]

    try:
        for _ in range(_MAX_TURNS):
            response = create_message(
                client,
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

            if response.stop_reason == "pause_turn":
                # Server-side web_search: results are injected server-side, no tool_result needed.
                continue

            if response.stop_reason == "tool_use":
                # Client-side tools only — web_search_20250305 is server-side so this
                # branch is only reached for other tool types if added later.
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
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
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start == -1 or end == 0:
        return _neutral(team_name)
    try:
        data = json.loads(text[start:end])
        raw_players = data.get("absent_players", [])
        absent_players = [
            {"name": str(p.get("name", "")), "reason": str(p.get("reason", ""))}
            for p in raw_players
            if isinstance(p, dict) and p.get("name")
        ]
        return {
            "absent_count":    int(data.get("absent_count", len(absent_players))),
            "absent_players":  absent_players,
            "rotation_signal": bool(data.get("rotation_signal", False)),
            "summary":         str(data.get("summary", "")),
            "sources":         [str(s) for s in data.get("sources", [])],
        }
    except (json.JSONDecodeError, ValueError):
        return _neutral(team_name)


def _neutral(team_name: str) -> dict:
    return {
        "absent_count":    0,
        "absent_players":  [],
        "rotation_signal": False,
        "summary":         "",
        "sources":         [],
    }
