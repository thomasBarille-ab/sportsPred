"""Serveur HTTP minimal pour déclencher les jobs à la demande et exposer un endpoint de chat.

Tourne dans un thread daemon aux côtés d'APScheduler.

Endpoints :
  POST /run/<job_id>   — déclenche un job immédiatement
  POST /chat           — chat avec l'agent Claude (tool use + DB)

Sécurité :
  Tous les POST requièrent l'en-tête X-Internal-Token égal à INTERNAL_API_TOKEN.
  Si la variable est absente, le serveur répond 503 (fail closed).
  Commandes de job explicites uniquement : /ingest, /predict, /evaluate, /retrain, /summary.
"""

from __future__ import annotations

import hmac
import json
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import structlog

from .db import session

if TYPE_CHECKING:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from .config import Settings

log = structlog.get_logger()

_ANTHROPIC_API_KEY: str = ""
_INTERNAL_TOKEN:    str = ""

_EXPLICIT_COMMANDS = {"/ingest", "/predict", "/evaluate", "/retrain", "/summary"}

_CHAT_MODEL      = "claude-haiku-4-5"
_CHAT_MAX_TURNS  = 6
_CHAT_MAX_TOKENS = 1024

_CHAT_SYSTEM = """\
Tu es l'assistant du système de prédiction sportive (Ligue 1 + NBA).
Tu réponds en français, de façon concise et directe.

Tu as accès à des outils pour interroger les données en temps réel :
prédictions, performances des modèles, matchs à venir, paris EV+.

Utilise les outils quand la question porte sur des données concrètes.
Pour les questions générales sur le fonctionnement du système, réponds directement.

Si l'utilisateur demande à déclencher un job, indique-lui d'utiliser
les commandes : /ingest · /predict · /evaluate · /retrain · /summary
"""

_CHAT_TOOLS: list[dict] = [
    {
        "name": "get_recent_predictions",
        "description": "Retourne les dernières prédictions avec résultat, probabilités et Brier score.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "enum": ["ligue1", "nba"]},
                "n": {"type": "integer", "description": "Nombre de prédictions (max 20)", "default": 10},
            },
            "required": ["sport"],
        },
    },
    {
        "name": "get_model_performance",
        "description": "Performances du modèle sur une fenêtre glissante : Brier score et accuracy par semaine.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "enum": ["ligue1", "nba"]},
                "days": {"type": "integer", "description": "Fenêtre en jours (ex: 30, 60, 90)", "default": 30},
            },
            "required": ["sport"],
        },
    },
    {
        "name": "get_failure_patterns",
        "description": "Compare les features moyennes entre prédictions correctes et incorrectes. Révèle les biais du modèle.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "enum": ["ligue1", "nba"]},
            },
            "required": ["sport"],
        },
    },
    {
        "name": "get_upcoming_fixtures",
        "description": "Matchs à venir avec leurs prédictions (si disponibles) et les cotes bookmaker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "enum": ["ligue1", "nba"]},
                "days": {"type": "integer", "description": "Horizon en jours (défaut 7)", "default": 7},
            },
            "required": ["sport"],
        },
    },
    {
        "name": "get_value_bets",
        "description": "Paris à valeur positive (EV > 0) en attente de résultat, triés par EV décroissant.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_betting_performance",
        "description": "Statistiques globales des simulations de paris : win rate, P&L total, EV moyen.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ── Implémentation des tools ──────────────────────────────────────────────────

def _serialize(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)


def _tool_get_recent_predictions(sport: str, n: int = 10) -> list[dict]:
    n = min(max(n, 1), 20)
    rows = session.fetch_all(
        """
        SELECT f.home_team_name, f.away_team_name, f.match_date,
               p.predicted_outcome,
               ROUND(p.prob_home_win::numeric, 2) AS p_home,
               ROUND(p.prob_draw::numeric, 2)     AS p_draw,
               ROUND(p.prob_away_win::numeric, 2) AS p_away,
               p.explanation,
               r.actual_outcome,
               ps.is_correct,
               ROUND(ps.brier_score::numeric, 4)  AS brier_score
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
    return [dict(r) for r in rows]


def _tool_get_model_performance(sport: str, days: int = 30) -> dict:
    days = min(max(days, 7), 365)
    summary = session.fetch_one(
        """
        SELECT COUNT(ps.id)                                         AS total,
               ROUND(AVG(ps.brier_score)::numeric, 4)               AS avg_brier,
               ROUND(AVG(ps.is_correct::int::float)::numeric, 3)    AS accuracy
        FROM prediction_scores ps
        JOIN fixtures f ON f.id = ps.fixture_id
        WHERE f.sport = %s
          AND f.match_date >= NOW() - (%s || ' days')::interval
        """,
        (sport, days),
    )
    weekly = session.fetch_all(
        f"""
        SELECT DATE_TRUNC('week', f.match_date)                     AS week,
               COUNT(ps.id)                                          AS n,
               ROUND(AVG(ps.brier_score)::numeric, 4)                AS avg_brier,
               ROUND(AVG(ps.is_correct::int::float)::numeric, 3)    AS accuracy
        FROM prediction_scores ps
        JOIN fixtures f ON f.id = ps.fixture_id
        WHERE f.sport = %s
          AND f.match_date >= NOW() - INTERVAL '{days} days'
        GROUP BY 1 ORDER BY 1
        """,
        (sport,),
    )
    model = session.fetch_one(
        "SELECT version, trained_at, training_samples FROM model_versions WHERE sport = %s AND is_production = TRUE LIMIT 1",
        (sport,),
    )
    return {
        "sport": sport,
        "window_days": days,
        "summary": dict(summary) if summary else {},
        "weekly_trend": [dict(r) for r in weekly],
        "production_model": dict(model) if model else {},
        "baselines": {"ligue1": 0.667, "nba": 0.25}.get(sport),
    }


def _tool_get_failure_patterns(sport: str) -> dict:
    rows = session.fetch_all(
        """
        SELECT ps.is_correct,
               COUNT(*)                                                            AS n,
               ROUND(AVG((p.features_snapshot->>'elo_diff')::float)::numeric, 1)  AS avg_elo_diff,
               ROUND(AVG(ps.brier_score)::numeric, 4)                             AS avg_brier,
               ROUND(AVG(p.prob_home_win)::numeric, 3)                            AS avg_conf_home
        FROM predictions p
        JOIN prediction_scores ps ON ps.prediction_id = p.id
        JOIN fixtures f ON f.id = p.fixture_id
        WHERE f.sport = %s AND p.features_snapshot IS NOT NULL
        GROUP BY ps.is_correct
        """,
        (sport,),
    )
    return {"sport": sport, "patterns": [dict(r) for r in rows]}


def _tool_get_upcoming_fixtures(sport: str, days: int = 7) -> list[dict]:
    days = min(max(days, 1), 30)
    rows = session.fetch_all(
        """
        SELECT f.home_team_name, f.away_team_name, f.match_date, f.round,
               p.predicted_outcome,
               ROUND(p.prob_home_win::numeric, 2) AS p_home,
               ROUND(p.prob_draw::numeric, 2)     AS p_draw,
               ROUND(p.prob_away_win::numeric, 2) AS p_away,
               p.explanation,
               mo.odds_home, mo.odds_draw, mo.odds_away, mo.bookmaker,
               bs.ev_pct, bs.bet_outcome AS recommended_bet
        FROM fixtures f
        LEFT JOIN predictions p ON p.fixture_id = f.id
        LEFT JOIN LATERAL (
            SELECT odds_home, odds_draw, odds_away, bookmaker
            FROM match_odds WHERE fixture_id = f.id
            ORDER BY CASE WHEN bookmaker = 'pinnacle' THEN 0 ELSE 1 END, fetched_at DESC
            LIMIT 1
        ) mo ON true
        LEFT JOIN bet_simulations bs ON bs.fixture_id = f.id
        WHERE f.sport = %s
          AND f.status IN ('SCHEDULED', 'TIMED')
          AND f.match_date BETWEEN NOW() AND NOW() + (%s || ' days')::interval
        ORDER BY f.match_date
        """,
        (sport, days),
    )
    return [dict(r) for r in rows]


def _tool_get_value_bets() -> list[dict]:
    rows = session.fetch_all(
        """
        SELECT f.sport, f.home_team_name, f.away_team_name, f.match_date,
               bs.bet_outcome, bs.odds_taken, bs.ev_pct, bs.bookmaker,
               p.predicted_outcome,
               ROUND(p.prob_home_win::numeric, 2) AS p_home,
               ROUND(p.prob_away_win::numeric, 2) AS p_away
        FROM bet_simulations bs
        JOIN fixtures f ON f.id = bs.fixture_id
        JOIN predictions p ON p.id = bs.prediction_id
        WHERE bs.status = 'pending' AND bs.ev_pct > 0
        ORDER BY bs.ev_pct DESC
        LIMIT 15
        """
    )
    return [dict(r) for r in rows]


def _tool_get_betting_performance() -> dict:
    stats = session.fetch_one(
        """
        SELECT COUNT(*)                                            AS total_bets,
               COUNT(*) FILTER (WHERE status = 'won')             AS won,
               COUNT(*) FILTER (WHERE status = 'lost')            AS lost,
               COUNT(*) FILTER (WHERE status = 'pending')         AS pending,
               ROUND(SUM(pnl_units)::numeric, 2)                  AS total_pnl,
               ROUND(AVG(ev_pct)::numeric, 4)                     AS avg_ev,
               ROUND(
                 COUNT(*) FILTER (WHERE status = 'won')::float
                 / NULLIF(COUNT(*) FILTER (WHERE status IN ('won','lost')), 0)::numeric,
               3)                                                  AS win_rate
        FROM bet_simulations
        """
    )
    return dict(stats) if stats else {}


def _execute_chat_tool(name: str, inputs: dict) -> Any:
    try:
        if name == "get_recent_predictions":
            return _tool_get_recent_predictions(inputs["sport"], inputs.get("n", 10))
        if name == "get_model_performance":
            return _tool_get_model_performance(inputs["sport"], inputs.get("days", 30))
        if name == "get_failure_patterns":
            return _tool_get_failure_patterns(inputs["sport"])
        if name == "get_upcoming_fixtures":
            return _tool_get_upcoming_fixtures(inputs["sport"], inputs.get("days", 7))
        if name == "get_value_bets":
            return _tool_get_value_bets()
        if name == "get_betting_performance":
            return _tool_get_betting_performance()
        return {"error": f"unknown tool: {name}"}
    except Exception as exc:
        log.warning("chat.tool_error", tool=name, error=str(exc))
        return {"error": str(exc)}


# ── Agent Claude conversationnel ──────────────────────────────────────────────

def _call_claude_agent(message: str, history: list[dict]) -> str:
    if not _ANTHROPIC_API_KEY:
        return "[ANTHROPIC_API_KEY non configurée — chat Claude indisponible]"

    try:
        import anthropic
    except ImportError:
        return "[Package anthropic non installé]"

    client = anthropic.Anthropic(api_key=_ANTHROPIC_API_KEY)

    messages: list[dict] = []
    for h in history[-8:]:
        messages.append(h)
    messages.append({"role": "user", "content": message})

    try:
        for _ in range(_CHAT_MAX_TURNS):
            response = client.messages.create(
                model=_CHAT_MODEL,
                max_tokens=_CHAT_MAX_TOKENS,
                system=_CHAT_SYSTEM,
                tools=_CHAT_TOOLS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text.strip()
                return ""

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        log.info("chat.tool_call", tool=block.name)
                        result = _execute_chat_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=_serialize),
                        })
                messages.append({"role": "user", "content": tool_results})
                continue

            break

    except Exception as exc:
        log.error("chat.claude_error", error=str(exc))
        return f"[Erreur Claude : {exc}]"

    return "[Réponse non disponible]"


# ── Handler HTTP ──────────────────────────────────────────────────────────────

def _check_token(handler: "BaseHTTPRequestHandler") -> bool:
    if not _INTERNAL_TOKEN:
        log.error("trigger_server.no_token_configured",
                  hint="Définir INTERNAL_API_TOKEN dans l'environnement")
        _send_json(handler, 503, {"error": "INTERNAL_API_TOKEN non configuré — serveur en mode fermé"})
        return False
    provided = handler.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(provided, _INTERNAL_TOKEN):
        log.warning("trigger_server.unauthorized", path=handler.path)
        _send_json(handler, 401, {"error": "Token invalide"})
        return False
    return True


def _send_json(handler: "BaseHTTPRequestHandler", code: int, body: dict) -> None:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _read_body(handler: "BaseHTTPRequestHandler", max_bytes: int = 65536) -> dict | None:
    try:
        length = int(handler.headers.get("Content-Length", 0))
    except ValueError:
        length = 0
    if length > max_bytes:
        _send_json(handler, 413, {"error": "Body trop volumineux (max 64 Ko)"})
        return None
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        _send_json(handler, 400, {"error": "JSON invalide"})
        return None


class _TriggerHandler(BaseHTTPRequestHandler):
    scheduler: "BlockingScheduler | None" = None

    def do_POST(self) -> None:
        if not _check_token(self):
            return

        parts = self.path.strip("/").split("/")

        if parts[0] == "run":
            self._handle_run(parts)
        elif parts[0] == "chat":
            self._handle_chat()
        else:
            _send_json(self, 404, {"error": "not found"})

    def _handle_run(self, parts: list[str]) -> None:
        if len(parts) < 2:
            _send_json(self, 400, {"error": "usage: /run/<job_id>"})
            return

        job_id = parts[1]
        scheduler = _TriggerHandler.scheduler
        if scheduler is None:
            _send_json(self, 503, {"error": "scheduler not ready"})
            return

        job = scheduler.get_job(job_id)
        if job is None:
            _send_json(self, 404, {"error": f"job inconnu: {job_id}"})
            return

        job.modify(next_run_time=datetime.now(timezone.utc))
        log.info("trigger.job_lancé", job=job_id)
        _send_json(self, 202, {"status": "accepted", "job": job_id})

    def _handle_chat(self) -> None:
        body = _read_body(self)
        if body is None:
            return

        raw_message = body.get("message", "")
        if not isinstance(raw_message, str):
            _send_json(self, 400, {"error": "message doit être une chaîne"})
            return
        message = raw_message.strip()[:2000]

        if not message:
            _send_json(self, 400, {"error": "message vide"})
            return

        raw_history = body.get("history", [])
        history: list[dict] = []
        if isinstance(raw_history, list):
            for item in raw_history[-8:]:
                if (
                    isinstance(item, dict)
                    and item.get("role") in ("user", "assistant")
                    and isinstance(item.get("content"), str)
                ):
                    history.append({
                        "role": item["role"],
                        "content": item["content"][:4000],
                    })

        # Commande explicite de job
        action: str | None = None
        action_note: str | None = None
        stripped = message.lstrip()
        if stripped in _EXPLICIT_COMMANDS:
            job_id = stripped[1:]
            scheduler = _TriggerHandler.scheduler
            if scheduler:
                job = scheduler.get_job(job_id)
                if job:
                    job.modify(next_run_time=datetime.now(timezone.utc))
                    action = job_id
                    action_note = "job lancé en arrière-plan"
                    log.info("chat.command_triggered", action=job_id)

        response = _call_claude_agent(message, history)

        _send_json(self, 200, {
            "response": response,
            "action": action,
            "action_note": action_note,
        })

    def log_message(self, *_: object) -> None:
        pass


# ── Point d'entrée ────────────────────────────────────────────────────────────

def start_trigger_server(
    scheduler: "BlockingScheduler",
    cfg: "Settings",
    port: int = 8080,
) -> None:
    global _ANTHROPIC_API_KEY, _INTERNAL_TOKEN
    _ANTHROPIC_API_KEY = cfg.anthropic_api_key or ""
    _INTERNAL_TOKEN    = cfg.internal_api_token

    if not _INTERNAL_TOKEN:
        log.error(
            "trigger_server.no_token",
            hint="INTERNAL_API_TOKEN absent — tous les appels POST renverront 503. "
                 "Générer avec : openssl rand -hex 32",
        )

    if not _ANTHROPIC_API_KEY:
        log.warning("trigger_server.no_anthropic_key",
                    hint="Chat Claude indisponible — configurer ANTHROPIC_API_KEY")

    _TriggerHandler.scheduler = scheduler
    server = ThreadingHTTPServer(("0.0.0.0", port), _TriggerHandler)
    threading.Thread(target=server.serve_forever, name="trigger-server", daemon=True).start()
    log.info("trigger_server.started", port=port, chat_backend="claude")
