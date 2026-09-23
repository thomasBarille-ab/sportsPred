"""Agent d'analyse post-match avec tool use Claude.

Analyse les performances du modèle après chaque journée :
- identifie les patterns d'échec
- compare features correctes vs incorrectes
- recommande des améliorations ou un retrain anticipé
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import structlog

from ..db import session

log = structlog.get_logger()

_MODEL = "claude-haiku-4-5"
_MAX_TURNS = 8
_MAX_TOKENS = 2048

_SYSTEM_PROMPT = """\
Tu es un agent d'analyse autonome pour un système de prédiction sportive ML (Ligue 1 + NBA).
Tu analyses les performances du modèle après chaque journée de matchs.

Utilise les outils disponibles pour investiguer les données, puis génère un rapport structuré.
Stratégie recommandée :
1. get_recent_predictions → vue d'ensemble des dernières prédictions
2. get_failure_patterns → compare les features entre prédictions correctes et incorrectes
3. get_model_performance_trend → vérifie si les performances se dégradent

Réponds UNIQUEMENT avec un objet JSON valide (sans markdown, sans ``` ni texte autour) :
{
  "summary": "Résumé en 1-2 phrases des performances récentes",
  "patterns": ["Pattern détecté 1", "Pattern détecté 2"],
  "recommendations": ["Recommandation feature engineering 1", "Recommandation 2"],
  "retrain_recommended": false,
  "retrain_reason": null
}

Les patterns doivent être concrets et actionnables (ex: "Le modèle rate les upsets quand away_b2b=1 : Brier moyen 0.58 vs 0.21 sur les prédictions correctes").
"""

_TOOLS: list[dict] = [
    {
        "name": "get_recent_predictions",
        "description": "Retourne les N dernières prédictions avec résultat, features clés et scores Brier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "enum": ["ligue1", "nba"], "description": "Sport à analyser"},
                "n": {"type": "integer", "description": "Nombre de prédictions (max 30)", "default": 20},
            },
            "required": ["sport"],
        },
    },
    {
        "name": "get_matchday_breakdown",
        "description": "Détail des prédictions pour une journée/round spécifique.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "enum": ["ligue1", "nba"]},
                "round": {"type": "integer", "description": "Numéro de la journée"},
            },
            "required": ["sport", "round"],
        },
    },
    {
        "name": "get_model_performance_trend",
        "description": "Évolution du Brier score et de l'accuracy sur une rolling window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "enum": ["ligue1", "nba"]},
                "days": {"type": "integer", "description": "Fenêtre temporelle en jours (ex: 30, 60)"},
            },
            "required": ["sport", "days"],
        },
    },
    {
        "name": "get_failure_patterns",
        "description": "Compare les features moyennes entre prédictions correctes et incorrectes. Révèle les biais systématiques du modèle.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "enum": ["ligue1", "nba"]},
            },
            "required": ["sport"],
        },
    },
]


def _serialize(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)


def _tool_get_recent_predictions(sport: str, n: int = 20) -> list[dict]:
    n = min(max(n, 1), 30)
    rows = session.fetch_all(
        """
        SELECT f.home_team_name, f.away_team_name, f.match_date, f.round,
               p.predicted_outcome,
               ROUND(p.prob_home_win::numeric, 2) AS p_home,
               ROUND(p.prob_draw::numeric, 2)     AS p_draw,
               ROUND(p.prob_away_win::numeric, 2) AS p_away,
               r.actual_outcome,
               ps.is_correct,
               ROUND(ps.brier_score::numeric, 4)  AS brier_score,
               p.features_snapshot
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        LEFT JOIN prediction_scores ps ON ps.prediction_id = p.id
        LEFT JOIN results r ON r.fixture_id = p.fixture_id
        WHERE f.sport = %s
        ORDER BY f.match_date DESC
        LIMIT %s
        """,
        (sport, n),
    )
    result = []
    for row in rows:
        r = dict(row)
        snapshot = r.pop("features_snapshot", None) or {}
        r["key_features"] = {
            k: round(float(v), 3) if isinstance(v, (int, float)) else v
            for k, v in snapshot.items()
            if k in (
                "elo_diff", "home_form_pts_last5", "away_form_pts_last5",
                "dc_p_home", "dc_p_draw", "dc_p_away",
                "home_b2b", "away_b2b",
                "home_win_rate_last10", "away_win_rate_last10",
            )
        }
        result.append(r)
    return result


def _tool_get_matchday_breakdown(sport: str, round: int) -> list[dict]:
    rows = session.fetch_all(
        """
        SELECT f.home_team_name, f.away_team_name, f.match_date,
               p.predicted_outcome,
               ROUND(p.prob_home_win::numeric, 2) AS p_home,
               ROUND(p.prob_draw::numeric, 2)     AS p_draw,
               ROUND(p.prob_away_win::numeric, 2) AS p_away,
               r.actual_outcome,
               ps.is_correct,
               ROUND(ps.brier_score::numeric, 4)  AS brier_score
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        LEFT JOIN prediction_scores ps ON ps.prediction_id = p.id
        LEFT JOIN results r ON r.fixture_id = p.fixture_id
        WHERE f.sport = %s AND f.round = %s
        ORDER BY f.match_date
        """,
        (sport, round),
    )
    return [dict(r) for r in rows]


def _tool_get_model_performance_trend(sport: str, days: int) -> list[dict]:
    days = min(max(days, 7), 365)
    rows = session.fetch_all(
        f"""
        SELECT DATE_TRUNC('week', f.match_date)                     AS week,
               COUNT(ps.id)                                          AS n,
               ROUND(AVG(ps.brier_score)::numeric, 4)                AS avg_brier,
               ROUND(AVG(ps.is_correct::int::float)::numeric, 3)    AS accuracy
        FROM prediction_scores ps
        JOIN fixtures f ON f.id = ps.fixture_id
        WHERE f.sport = %s
          AND f.match_date >= NOW() - INTERVAL '{days} days'
        GROUP BY 1
        ORDER BY 1
        """,
        (sport,),
    )
    return [dict(r) for r in rows]


def _tool_get_failure_patterns(sport: str) -> dict:
    rows = session.fetch_all(
        """
        SELECT
            ps.is_correct,
            COUNT(*)                                                 AS n,
            ROUND(AVG((p.features_snapshot->>'elo_diff')::float)::numeric, 1)    AS avg_elo_diff,
            ROUND(AVG(p.prob_home_win)::numeric, 3)                              AS avg_conf_home,
            ROUND(MAX(p.prob_home_win)::numeric, 3)                              AS max_conf,
            ROUND(MIN(p.prob_home_win)::numeric, 3)                              AS min_conf,
            ROUND(AVG(ps.brier_score)::numeric, 4)                               AS avg_brier
        FROM predictions p
        JOIN prediction_scores ps ON ps.prediction_id = p.id
        JOIN fixtures f ON f.id = p.fixture_id
        WHERE f.sport = %s
          AND p.features_snapshot IS NOT NULL
        GROUP BY ps.is_correct
        """,
        (sport,),
    )
    # features spécifiques par sport
    if sport == "ligue1":
        extra = session.fetch_all(
            """
            SELECT
                ps.is_correct,
                ROUND(AVG((p.features_snapshot->>'home_form_pts_last5')::float)::numeric, 2) AS avg_home_form,
                ROUND(AVG((p.features_snapshot->>'away_form_pts_last5')::float)::numeric, 2) AS avg_away_form,
                ROUND(AVG((p.features_snapshot->>'home_b2b')::float)::numeric, 3)            AS avg_home_b2b,
                ROUND(AVG((p.features_snapshot->>'away_b2b')::float)::numeric, 3)            AS avg_away_b2b
            FROM predictions p
            JOIN prediction_scores ps ON ps.prediction_id = p.id
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE f.sport = %s AND p.features_snapshot IS NOT NULL
            GROUP BY ps.is_correct
            """,
            (sport,),
        )
        extra_map = {r["is_correct"]: dict(r) for r in extra}
    else:
        extra = session.fetch_all(
            """
            SELECT
                ps.is_correct,
                ROUND(AVG((p.features_snapshot->>'home_win_rate_last10')::float)::numeric, 3) AS avg_home_wr,
                ROUND(AVG((p.features_snapshot->>'away_win_rate_last10')::float)::numeric, 3) AS avg_away_wr,
                ROUND(AVG((p.features_snapshot->>'home_b2b')::float)::numeric, 3)             AS avg_home_b2b,
                ROUND(AVG((p.features_snapshot->>'away_b2b')::float)::numeric, 3)             AS avg_away_b2b
            FROM predictions p
            JOIN prediction_scores ps ON ps.prediction_id = p.id
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE f.sport = %s AND p.features_snapshot IS NOT NULL
            GROUP BY ps.is_correct
            """,
            (sport,),
        )
        extra_map = {r["is_correct"]: dict(r) for r in extra}

    result = []
    for row in rows:
        r = dict(row)
        r.update(extra_map.get(row["is_correct"], {}))
        result.append(r)
    return {"sport": sport, "patterns": result}


def _execute_tool(name: str, inputs: dict) -> Any:
    try:
        if name == "get_recent_predictions":
            return _tool_get_recent_predictions(inputs["sport"], inputs.get("n", 20))
        if name == "get_matchday_breakdown":
            return _tool_get_matchday_breakdown(inputs["sport"], inputs["round"])
        if name == "get_model_performance_trend":
            return _tool_get_model_performance_trend(inputs["sport"], inputs["days"])
        if name == "get_failure_patterns":
            return _tool_get_failure_patterns(inputs["sport"])
        return {"error": f"unknown tool: {name}"}
    except Exception as exc:
        log.warning("agent.tool_error", tool=name, error=str(exc))
        return {"error": str(exc)}


def run_agent_analysis(api_key: str) -> dict | None:
    """Lance l'agent d'analyse post-match. Retourne le rapport structuré ou None."""
    if not api_key:
        log.warning("agent_analysis.skipped", reason="ANTHROPIC_API_KEY non configurée")
        return None

    try:
        import anthropic
    except ImportError:
        log.error("agent_analysis.import_error", reason="anthropic package non installé")
        return None

    client = anthropic.Anthropic(api_key=api_key)

    user_message = (
        "Analyse les performances récentes du modèle de prédiction pour la Ligue 1 et la NBA. "
        "Identifie les patterns d'échec, compare avec les baselines (Ligue 1 : Brier 0.667, NBA : 0.25) "
        "et génère des recommandations concrètes."
    )
    messages = [{"role": "user", "content": user_message}]

    try:
        for turn in range(_MAX_TURNS):
            response = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                tools=_TOOLS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Extraire le texte final et parser le JSON
                for block in response.content:
                    if block.type == "text":
                        text = block.text.strip()
                        try:
                            report = json.loads(text)
                            log.info("agent_analysis.done", turns=turn + 1)
                            return report
                        except json.JSONDecodeError:
                            log.warning("agent_analysis.json_parse_error", text=text[:200])
                            return {"summary": text, "patterns": [], "recommendations": [], "retrain_recommended": False, "retrain_reason": None}
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        log.info("agent.tool_call", tool=block.name, inputs=str(block.input)[:120])
                        result = _execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=_serialize),
                        })
                messages.append({"role": "user", "content": tool_results})
                continue

            break  # stop_reason inattendu

    except Exception as exc:
        log.error("agent_analysis.error", error=str(exc))
        return None

    log.warning("agent_analysis.max_turns_reached", turns=_MAX_TURNS)
    return None
