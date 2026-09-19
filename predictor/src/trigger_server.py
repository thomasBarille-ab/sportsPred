"""Serveur HTTP minimal pour déclencher les jobs à la demande et exposer un endpoint de chat.

Tourne dans un thread daemon aux côtés d'APScheduler.

Endpoints :
  POST /run/<job_id>   — déclenche un job immédiatement
  POST /chat           — chat avec l'agent (Ollama + contexte DB)

Sécurité :
  Tous les POST requièrent l'en-tête X-Internal-Token égal à INTERNAL_API_TOKEN.
  Si la variable est absente, le serveur répond 503 (fail closed).
  Commandes de job explicites uniquement : /ingest, /predict, /evaluate, /retrain, /summary.
"""

from __future__ import annotations

import hmac
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from .db import session

if TYPE_CHECKING:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from .config import Settings

log = structlog.get_logger()

# Configurés au démarrage via start_trigger_server()
_OLLAMA_URL:        str = "http://ollama:11434"
_OLLAMA_MODEL:      str = "llama3.2:3b"
_INTERNAL_TOKEN:    str = ""

_EXPLICIT_COMMANDS = {"/ingest", "/predict", "/evaluate", "/retrain", "/summary"}


# ── Contexte DB pour le chat ──────────────────────────────────────────────────

def _build_context() -> str:
    lines: list[str] = []

    models = session.fetch_all(
        """
        SELECT sport, version, trained_at,
               ROUND(holdout_brier::numeric, 4)    AS brier,
               ROUND(holdout_accuracy::numeric, 3) AS accuracy,
               training_samples
        FROM model_versions
        WHERE is_production = TRUE
        ORDER BY sport
        """
    )
    if models:
        lines.append("=== MODÈLES EN PRODUCTION ===")
        for m in models:
            sport_label = "Ligue 1" if m["sport"] == "ligue1" else "NBA"
            lines.append(
                f"  {sport_label}: {m['version']} | "
                f"accuracy={float(m['accuracy'] or 0):.1%} | "
                f"Brier={float(m['brier'] or 0):.4f} | "
                f"entraîné sur {m['training_samples']} matchs"
            )

    recent = session.fetch_all(
        """
        SELECT f.sport, f.home_team_name, f.away_team_name, f.match_date,
               p.predicted_outcome,
               ROUND(p.prob_home_win::numeric, 2) AS p_home,
               ROUND(p.prob_draw::numeric, 2)     AS p_draw,
               ROUND(p.prob_away_win::numeric, 2) AS p_away,
               r.actual_outcome,
               ps.is_correct,
               ROUND(ps.brier_score::numeric, 4)  AS brier
        FROM predictions p
        JOIN fixtures f       ON f.id = p.fixture_id
        LEFT JOIN results r   ON r.fixture_id = p.fixture_id
        LEFT JOIN prediction_scores ps ON ps.prediction_id = p.id
        ORDER BY f.match_date DESC
        LIMIT 10
        """
    )
    if recent:
        lines.append("\n=== PRÉDICTIONS RÉCENTES ===")
        for r in recent:
            sport_label = "L1" if r["sport"] == "ligue1" else "NBA"
            date_str = str(r["match_date"])[:10]
            verdict = ""
            if r["is_correct"] is True:  verdict = " ✓"
            elif r["is_correct"] is False: verdict = " ✗"
            probs = f"dom={float(r['p_home'] or 0):.0%}"
            if r["p_draw"] is not None:
                probs += f" nul={float(r['p_draw'] or 0):.0%}"
            probs += f" ext={float(r['p_away'] or 0):.0%}"
            actual = f" → réel: {r['actual_outcome']}" if r["actual_outcome"] else " → match à venir"
            lines.append(
                f"  [{sport_label}] {date_str} | "
                f"{r['home_team_name']} vs {r['away_team_name']} | "
                f"prédit: {r['predicted_outcome']} ({probs})"
                f"{actual}{verdict}"
            )

    upcoming_no_pred = session.fetch_all(
        """
        SELECT f.sport, f.home_team_name, f.away_team_name, f.match_date
        FROM fixtures f
        LEFT JOIN predictions p ON p.fixture_id = f.id
        WHERE p.id IS NULL
          AND f.match_date >= NOW()
          AND f.match_date <= NOW() + INTERVAL '7 days'
          AND f.status IN ('SCHEDULED','TIMED')
        ORDER BY f.match_date
        LIMIT 5
        """
    )
    if upcoming_no_pred:
        lines.append("\n=== MATCHS À VENIR SANS PRÉDICTION ===")
        for u in upcoming_no_pred:
            sport_label = "L1" if u["sport"] == "ligue1" else "NBA"
            date_str = str(u["match_date"])[:16].replace("T", " ")
            lines.append(f"  [{sport_label}] {date_str} | {u['home_team_name']} vs {u['away_team_name']}")

    last_logs = session.fetch_all(
        """
        SELECT job_name, sport, status, duration_seconds, records_processed, started_at
        FROM agent_logs
        ORDER BY started_at DESC
        LIMIT 8
        """
    )
    if last_logs:
        lines.append("\n=== DERNIERS JOBS ===")
        for l in last_logs:
            sport_str = f" ({l['sport']})" if l["sport"] else ""
            dur = f" {round(float(l['duration_seconds']), 1)}s" if l["duration_seconds"] else ""
            rec = f" {l['records_processed']} records" if l["records_processed"] else ""
            ts = str(l["started_at"])[:16].replace("T", " ")
            lines.append(f"  {ts} | {l['job_name']}{sport_str} → {l['status']}{dur}{rec}")

    return "\n".join(lines)


# ── Appel Ollama ──────────────────────────────────────────────────────────────

def _call_ollama(
    message: str,
    history: list[dict],
    context: str,
) -> str:
    system = f"""Tu es l'agent de prédiction sportive. Tu surveilles et analyses en temps réel
les performances des modèles de prédiction Ligue 1 et NBA.

{context}

Pour déclencher un job, l'utilisateur doit envoyer une commande EXACTE :
  /ingest    — récupérer les données récentes
  /predict   — générer les prédictions
  /evaluate  — scorer les prédictions dont le résultat est connu
  /retrain   — réentraîner les modèles XGBoost
  /summary   — générer le résumé Ollama

Tu ne déclenches aucune action de toi-même : toute action passe par ces commandes explicites.
Réponds toujours en français, de façon concise et directe.
Si tu ne sais pas quelque chose, dis-le clairement."""

    messages: list[dict] = [{"role": "system", "content": system}]
    for h in history[-10:]:
        messages.append(h)
    messages.append({"role": "user", "content": message})

    try:
        resp = httpx.post(
            f"{_OLLAMA_URL}/api/chat",
            json={
                "model": _OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.6, "num_predict": 500},
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    except Exception as exc:
        log.error("chat.ollama_error", error=str(exc))
        return f"[Erreur Ollama : {exc}]"


# ── Handler HTTP ──────────────────────────────────────────────────────────────

def _check_token(handler: "BaseHTTPRequestHandler") -> bool:
    """Vérifie X-Internal-Token. Retourne True si valide, répond 401/503 sinon."""
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
    """Lit et parse le body JSON. Retourne None si invalide ou trop grand."""
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

        # Déclenche via APScheduler (pas de thread direct)
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

        # Filtrage de l'historique : rôles user/assistant, contenu string, 10 msg max, 4000 chars max
        raw_history = body.get("history", [])
        history: list[dict] = []
        if isinstance(raw_history, list):
            for item in raw_history[-10:]:
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
            job_id = stripped[1:]  # retire le /
            scheduler = _TriggerHandler.scheduler
            if scheduler:
                job = scheduler.get_job(job_id)
                if job:
                    job.modify(next_run_time=datetime.now(timezone.utc))
                    action = job_id
                    action_note = "job lancé en arrière-plan"
                    log.info("chat.command_triggered", action=job_id)

        try:
            context = _build_context()
        except Exception as exc:
            context = f"[Erreur lors de la récupération du contexte: {exc}]"

        response = _call_ollama(message, history, context)

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
    global _OLLAMA_URL, _OLLAMA_MODEL, _INTERNAL_TOKEN
    _OLLAMA_URL    = cfg.ollama_url
    _OLLAMA_MODEL  = cfg.ollama_model
    _INTERNAL_TOKEN = cfg.internal_api_token

    if not _INTERNAL_TOKEN:
        log.error(
            "trigger_server.no_token",
            hint="INTERNAL_API_TOKEN absent — tous les appels POST renverront 503. "
                 "Générer avec : openssl rand -hex 32",
        )

    _TriggerHandler.scheduler = scheduler
    server = ThreadingHTTPServer(("0.0.0.0", port), _TriggerHandler)
    threading.Thread(target=server.serve_forever, name="trigger-server", daemon=True).start()
    log.info("trigger_server.started", port=port, ollama_model=_OLLAMA_MODEL)
