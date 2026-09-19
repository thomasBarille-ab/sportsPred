"""Serveur HTTP minimal pour déclencher les jobs à la demande et exposer un endpoint de chat.

Tourne dans un thread daemon aux côtés d'APScheduler.

Endpoints :
  POST /run/<job_id>   — déclenche un job immédiatement
  POST /chat           — chat avec l'agent (Ollama + contexte DB)
"""

from __future__ import annotations

import json
import threading
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
_OLLAMA_URL:   str = "http://ollama:11434"
_OLLAMA_MODEL: str = "llama3.2:3b"


# ── Détection d'intent ────────────────────────────────────────────────────────

_ACTION_KEYWORDS: dict[str, list[str]] = {
    "ingest":   ["ingest", "ingère", "ingérer", "données récentes", "mise à jour",
                 "récupère les données", "maj données", "nouvelles données"],
    "predict":  ["prédi", "prédictions", "génère les prédictions", "calcule les prédictions",
                 "nouvelles prédictions"],
    "evaluate": ["évalue", "évaluation", "score les prédictions", "note les résultats",
                 "calcule les scores"],
    "retrain":  ["réentraîne", "réentraînement", "retrain", "entraîne le modèle",
                 "nouveau modèle", "réentraîner"],
}

def _detect_action(message: str) -> str | None:
    low = message.lower()
    for action, keywords in _ACTION_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            return action
    return None


# ── Contexte DB pour le chat ──────────────────────────────────────────────────

def _build_context() -> str:
    lines: list[str] = []

    # Modèles en production
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

    # Prédictions récentes (10 dernières avec résultat connu)
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

    # Prochains matchs sans prédiction
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

    # Derniers logs
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
    action_triggered: str | None,
    action_note: str | None,
) -> str:
    system = f"""Tu es l'agent de prédiction sportive. Tu surveilles et analyses en temps réel
les performances des modèles de prédiction Ligue 1 et NBA.

{context}

Tu peux déclencher ces actions si l'utilisateur le demande :
  - "ingest"   : récupérer les données récentes depuis les APIs
  - "predict"  : générer les prédictions pour les matchs à venir
  - "evaluate" : scorer les prédictions dont le résultat est connu
  - "retrain"  : réentraîner les modèles XGBoost

Réponds toujours en français, de façon concise et directe.
Si tu ne sais pas quelque chose, dis-le clairement."""

    messages: list[dict] = [{"role": "system", "content": system}]

    # Injecte les 10 derniers échanges pour la mémoire de conversation
    for h in history[-10:]:
        messages.append(h)

    # Message utilisateur enrichi si une action a été détectée
    user_content = message
    if action_triggered and action_note:
        user_content = f"{message}\n\n[Système: l'action '{action_triggered}' a été déclenchée automatiquement — {action_note}]"
    messages.append({"role": "user", "content": user_content})

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

class _TriggerHandler(BaseHTTPRequestHandler):
    scheduler: "BlockingScheduler | None" = None

    def do_POST(self) -> None:
        parts = self.path.strip("/").split("/")

        if parts[0] == "run":
            self._handle_run(parts)
        elif parts[0] == "chat":
            self._handle_chat()
        else:
            self._respond(404, {"error": "not found"})

    def _handle_run(self, parts: list[str]) -> None:
        if len(parts) < 2:
            self._respond(400, {"error": "usage: /run/<job_id>"})
            return

        job_id = parts[1]
        scheduler = _TriggerHandler.scheduler
        if scheduler is None:
            self._respond(503, {"error": "scheduler not ready"})
            return

        job = scheduler.get_job(job_id)
        if job is None:
            self._respond(404, {"error": f"job inconnu: {job_id}"})
            return

        threading.Thread(target=job.func, name=f"trigger-{job_id}", daemon=True).start()
        log.info("trigger.job_lancé", job=job_id)
        self._respond(200, {"status": "triggered", "job": job_id})

    def _handle_chat(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body: dict[str, Any] = json.loads(self.rfile.read(length) or b"{}")
        message: str = body.get("message", "").strip()
        history: list[dict] = body.get("history", [])

        if not message:
            self._respond(400, {"error": "message vide"})
            return

        # Détection d'action
        action = _detect_action(message)
        action_note: str | None = None
        if action:
            scheduler = _TriggerHandler.scheduler
            if scheduler:
                job = scheduler.get_job(action)
                if job:
                    threading.Thread(target=job.func, name=f"trigger-{action}", daemon=True).start()
                    action_note = f"job lancé en arrière-plan"
                    log.info("chat.action_triggered", action=action)

        # Contexte DB
        try:
            context = _build_context()
        except Exception as exc:
            context = f"[Erreur lors de la récupération du contexte: {exc}]"

        # Réponse Ollama
        response = _call_ollama(message, history, context, action, action_note)

        self._respond(200, {
            "response": response,
            "action": action,
            "action_note": action_note,
        })

    def _respond(self, code: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_: object) -> None:
        pass


# ── Point d'entrée ────────────────────────────────────────────────────────────

def start_trigger_server(
    scheduler: "BlockingScheduler",
    cfg: "Settings",
    port: int = 8080,
) -> None:
    global _OLLAMA_URL, _OLLAMA_MODEL
    _OLLAMA_URL   = cfg.ollama_url
    _OLLAMA_MODEL = cfg.ollama_model

    _TriggerHandler.scheduler = scheduler
    server = ThreadingHTTPServer(("0.0.0.0", port), _TriggerHandler)
    threading.Thread(target=server.serve_forever, name="trigger-server", daemon=True).start()
    log.info("trigger_server.started", port=port, ollama_model=_OLLAMA_MODEL)
