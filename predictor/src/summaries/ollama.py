"""Génération du résumé quotidien des performances via Ollama / Llama 3.2."""

from __future__ import annotations

import json
from datetime import date

import httpx
import structlog

log = structlog.get_logger()


def build_prompt(metrics: dict) -> str:
    """Construit le prompt à partir des métriques agrégées."""
    l1 = metrics.get("ligue1", {})
    nba = metrics.get("nba", {})

    return f"""Tu es un analyste de performance pour un système de prédiction sportive.
Rédige un résumé concis (5-8 phrases) en français des performances du jour.

Données Ligue 1 (7 derniers jours) :
- Prédictions totales : {l1.get('n_predictions', 0)}
- Accuracy : {l1.get('accuracy', 0):.1%}
- Brier score moyen : {l1.get('avg_brier', 0):.4f}
- Log-loss moyen : {l1.get('avg_logloss', 0):.4f}

Données NBA (7 derniers jours) :
- Prédictions totales : {nba.get('n_predictions', 0)}
- Accuracy : {nba.get('accuracy', 0):.1%}
- Brier score moyen : {nba.get('avg_brier', 0):.4f}
- Log-loss moyen : {nba.get('avg_logloss', 0):.4f}

Rappel : un Brier score < 0.20 est excellent pour le foot (3 résultats possibles),
un score < 0.22 est excellent pour le basket (2 résultats).
Le random baseline est ~0.67 pour le foot et ~0.25 pour le basket.

Résumé :"""


def generate_summary(
    ollama_url: str,
    model: str,
    metrics: dict,
    timeout: float = 120.0,
) -> str:
    """Appelle Ollama pour générer le résumé. Retourne le texte généré."""
    prompt = build_prompt(metrics)

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 300,
        },
    }

    log.info("ollama.generate_start", model=model, ollama_url=ollama_url)
    try:
        resp = httpx.post(
            f"{ollama_url}/api/generate",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        log.info("ollama.generate_done", chars=len(text))
        return text
    except Exception as exc:
        log.error("ollama.generate_failed", error=str(exc))
        return f"[Résumé indisponible — erreur Ollama : {exc}]"
