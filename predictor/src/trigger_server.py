"""Serveur FastAPI pour déclencher les jobs à la demande et exposer un endpoint de chat.

Tourne dans un thread daemon aux côtés d'APScheduler.

Endpoints :
  POST /run/{job_id}   — déclenche un job immédiatement
  POST /chat           — chat avec l'agent Claude (tool use + DB, SSE ou JSON)
  GET  /docs           — documentation Swagger automatique

Sécurité :
  Tous les POST requièrent l'en-tête X-Internal-Token égal à INTERNAL_API_TOKEN.
  Si la variable est absente, le serveur répond 503 (fail closed).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import threading
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import structlog
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .db import session
from .db.queries import q_failure_patterns, q_recent_predictions, q_upcoming_fixtures
from .llm_client import create_message

if TYPE_CHECKING:
    from apscheduler.schedulers.blocking import BlockingScheduler

    from .config import Settings

log = structlog.get_logger()

app = FastAPI(title="Sports Predictor", version="1.0", docs_url="/docs")

_ANTHROPIC_API_KEY: str = ""
_INTERNAL_TOKEN:    str = ""
_scheduler: BlockingScheduler | None = None

_EXPLICIT_COMMANDS = {"/ingest", "/predict", "/evaluate", "/retrain", "/summary", "/context"}

_CHAT_MODEL      = "claude-haiku-4-5"
_CHAT_MAX_TURNS  = 6
_CHAT_MAX_TOKENS = 1024

_CHAT_SYSTEM = """\
Tu es l'assistant du système de prédiction sportive (Ligue 1, NBA, Équipe de France NT).
Tu réponds en français, de façon concise et directe.

Tu as accès à des outils pour interroger les données en temps réel :
prédictions, performances des modèles, matchs à venir, paris EV+.

Utilise les outils quand la question porte sur des données concrètes.
Pour les questions générales sur le fonctionnement du système, réponds directement.

Si l'utilisateur demande à déclencher un job, indique-lui d'utiliser
les commandes : /ingest · /predict · /evaluate · /retrain · /summary · /context
"""

_CHAT_TOOLS: list[dict] = [
    {
        "name": "get_recent_predictions",
        "description": "Retourne les dernières prédictions avec résultat, probabilités et Brier score.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "enum": ["ligue1", "nba", "france_nt"]},
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
                "sport": {"type": "string", "enum": ["ligue1", "nba", "france_nt"]},
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
                "sport": {"type": "string", "enum": ["ligue1", "nba", "france_nt"]},
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
                "sport": {"type": "string", "enum": ["ligue1", "nba", "france_nt"]},
                "days": {"type": "integer", "description": "Horizon en jours (défaut 7)", "default": 7},
            },
            "required": ["sport"],
        },
    },
    {
        "name": "get_value_bets",
        "description": "Paris à valeur positive (EV > 0) en attente de résultat, triés par EV décroissant.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_betting_performance",
        "description": "Statistiques globales des simulations de paris : win rate, P&L total, EV moyen.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


# ── Pydantic models ────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=16)
    stream: bool = False


# ── Auth dependency ────────────────────────────────────────────────────────────

def verify_token(x_internal_token: str = Header(default="")) -> None:
    if not _INTERNAL_TOKEN:
        raise HTTPException(503, detail="INTERNAL_API_TOKEN non configuré — serveur en mode fermé")
    if not hmac.compare_digest(x_internal_token, _INTERNAL_TOKEN):
        raise HTTPException(401, detail="Token invalide")


# ── Tool helpers ───────────────────────────────────────────────────────────────

def _serialize(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)


def _tool_get_recent_predictions(sport: str, n: int = 10) -> list[dict]:
    rows = q_recent_predictions(sport, min(n, 20))
    for row in rows:
        row.pop("features_snapshot", None)
    return rows


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
        "baselines": {"ligue1": 0.667, "nba": 0.25, "france_nt": 0.667}.get(sport),
    }


def _tool_get_failure_patterns(sport: str) -> dict:
    return {"sport": sport, "patterns": q_failure_patterns(sport)}


def _tool_get_upcoming_fixtures(sport: str, days: int = 7) -> list[dict]:
    return q_upcoming_fixtures(sport, days)


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


def _execute_tool(name: str, inputs: dict) -> Any:
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


# ── Agent Claude helpers ───────────────────────────────────────────────────────

def _run_tool_loop(client: Any, messages: list[dict]) -> list[dict]:
    for _ in range(_CHAT_MAX_TURNS - 1):
        resp = create_message(
            client,
            model=_CHAT_MODEL,
            max_tokens=256,
            system=_CHAT_SYSTEM,
            tools=_CHAT_TOOLS,
            messages=messages,
        )
        if resp.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                log.info("chat.tool_call", tool=block.name)
                result = _execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=_serialize),
                })
        messages.append({"role": "user", "content": tool_results})
    return messages


def _call_claude_agent(message: str, history: list[dict]) -> str:
    if not _ANTHROPIC_API_KEY:
        return "[ANTHROPIC_API_KEY non configurée — chat Claude indisponible]"
    try:
        import anthropic
    except ImportError:
        return "[Package anthropic non installé]"

    client = anthropic.Anthropic(api_key=_ANTHROPIC_API_KEY)
    messages = list(history) + [{"role": "user", "content": message}]

    try:
        for _ in range(_CHAT_MAX_TURNS):
            response = create_message(
                client,
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
                        result = _execute_tool(block.name, block.input)
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


async def _stream_claude_agent(message: str, history: list[dict], action: str | None, action_note: str | None) -> AsyncIterator[str]:
    if not _ANTHROPIC_API_KEY:
        yield f"data: {json.dumps({'chunk': '[ANTHROPIC_API_KEY non configurée]'})}\n\n"
        yield f"data: {json.dumps({'done': True, 'action': action, 'action_note': action_note})}\n\n"
        return

    try:
        import anthropic
    except ImportError:
        yield f"data: {json.dumps({'chunk': '[Package anthropic non installé]'})}\n\n"
        yield f"data: {json.dumps({'done': True, 'action': action, 'action_note': action_note})}\n\n"
        return

    client = anthropic.Anthropic(api_key=_ANTHROPIC_API_KEY)
    messages = list(history) + [{"role": "user", "content": message}]

    try:
        loop = asyncio.get_event_loop()
        messages = await loop.run_in_executor(None, _run_tool_loop, client, messages)

        with client.messages.stream(
            model=_CHAT_MODEL,
            max_tokens=_CHAT_MAX_TOKENS,
            system=_CHAT_SYSTEM,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield f"data: {json.dumps({'chunk': text})}\n\n"

    except Exception as exc:
        log.error("chat.stream_error", error=str(exc))
        yield f"data: {json.dumps({'chunk': f'[Erreur : {exc}]'})}\n\n"

    yield f"data: {json.dumps({'done': True, 'action': action, 'action_note': action_note})}\n\n"


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.post("/run/{job_id}", dependencies=[Depends(verify_token)])
async def run_job(job_id: str = Path(...)) -> dict:
    if _scheduler is None:
        raise HTTPException(503, detail="scheduler not ready")
    job = _scheduler.get_job(job_id)
    if job is None:
        raise HTTPException(404, detail=f"job inconnu: {job_id}")
    job.modify(next_run_time=datetime.now(timezone.utc))
    log.info("trigger.job_lancé", job=job_id)
    return {"status": "accepted", "job": job_id}


@app.post("/chat", dependencies=[Depends(verify_token)])
async def chat(body: ChatRequest, request: Request) -> Any:
    history = [{"role": m.role, "content": m.content} for m in body.history]
    message = body.message

    # Commande explicite de job
    action: str | None = None
    action_note: str | None = None
    stripped = message.lstrip()
    if stripped in _EXPLICIT_COMMANDS:
        job_id = stripped[1:]
        if _scheduler:
            job = _scheduler.get_job(job_id)
            if job:
                job.modify(next_run_time=datetime.now(timezone.utc))
                action = job_id
                action_note = "job lancé en arrière-plan"
                log.info("chat.command_triggered", action=job_id)

    wants_stream = body.stream or "text/event-stream" in request.headers.get("accept", "")

    if wants_stream:
        return StreamingResponse(
            _stream_claude_agent(message, history, action, action_note),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, _call_claude_agent, message, history)
    return {"response": response, "action": action, "action_note": action_note}


# ── Launcher ──────────────────────────────────────────────────────────────────

def start_trigger_server(
    scheduler: BlockingScheduler,
    cfg: Settings,
    port: int = 8080,
) -> None:
    global _ANTHROPIC_API_KEY, _INTERNAL_TOKEN, _scheduler
    _ANTHROPIC_API_KEY = cfg.anthropic_api_key or ""
    _INTERNAL_TOKEN    = cfg.internal_api_token
    _scheduler         = scheduler

    if not _INTERNAL_TOKEN:
        log.error(
            "trigger_server.no_token",
            hint="INTERNAL_API_TOKEN absent — tous les appels POST renverront 503. "
                 "Générer avec : openssl rand -hex 32",
        )
    if not _ANTHROPIC_API_KEY:
        log.warning("trigger_server.no_anthropic_key",
                    hint="Chat Claude indisponible — configurer ANTHROPIC_API_KEY")

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(
        target=server.run,
        name="trigger-server",
        daemon=True,
    )
    thread.start()
    log.info("trigger_server.started", port=port, chat_backend="claude")
