"""Serveur HTTP minimal pour déclencher les jobs à la demande.

Tourne dans un thread daemon aux côtés d'APScheduler.
Endpoint : POST /run/<job_id>
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from apscheduler.schedulers.blocking import BlockingScheduler

log = structlog.get_logger()


class _TriggerHandler(BaseHTTPRequestHandler):
    scheduler: "BlockingScheduler | None" = None

    def do_POST(self) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) < 2 or parts[0] != "run":
            self._respond(404, {"error": "invalid path, use /run/<job_id>"})
            return

        job_id = parts[1]
        scheduler = _TriggerHandler.scheduler

        if scheduler is None:
            self._respond(503, {"error": "scheduler not ready"})
            return

        job = scheduler.get_job(job_id)
        if job is None:
            self._respond(404, {"error": f"unknown job: {job_id}"})
            return

        # Lance le job dans un thread séparé pour ne pas bloquer la réponse HTTP
        thread = threading.Thread(target=job.func, name=f"trigger-{job_id}", daemon=True)
        thread.start()

        log.info("trigger.job_lancé", job=job_id)
        self._respond(200, {"status": "triggered", "job": job_id})

    def _respond(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_: object) -> None:
        pass  # silence les logs HTTP access


def start_trigger_server(scheduler: "BlockingScheduler", port: int = 8080) -> None:
    _TriggerHandler.scheduler = scheduler
    server = ThreadingHTTPServer(("0.0.0.0", port), _TriggerHandler)
    thread = threading.Thread(target=server.serve_forever, name="trigger-server", daemon=True)
    thread.start()
    log.info("trigger_server.started", port=port)
