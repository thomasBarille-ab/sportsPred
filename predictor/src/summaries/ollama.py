"""Génération du résumé quotidien des performances via Ollama / Llama 3.2."""

from __future__ import annotations

import json
from datetime import date

import httpx
import structlog

from ..scoring.metrics import BRIER_BASELINE

log = structlog.get_logger()


def build_prompt(metrics: dict) -> str:
    """Construit le prompt à partir des métriques agrégées."""
    l1  = metrics.get("ligue1")
    nba = metrics.get("nba")

    def sport_block(label: str, data: dict | None, sport_key: str) -> str:
        baseline = BRIER_BASELINE[sport_key]
        if not data:
            return f"Données {label} : aucune prédiction évaluée sur les 7 derniers jours — ne pas tirer de conclusion sur ce sport."
        return (
            f"Données {label} (7 derniers jours) :\n"
            f"- Prédictions scorées : {int(data.get('n_predictions', 0))}\n"
            f"- Accuracy : {data.get('accuracy', 0):.1%}\n"
            f"- Brier score moyen : {data.get('avg_brier', 0):.4f} "
            f"(baseline modèle aléatoire = {baseline:.4f})\n"
            f"- Log-loss moyen : {data.get('avg_logloss', 0):.4f}"
        )

    l1_block  = sport_block("Ligue 1", l1,  "ligue1")
    nba_block = sport_block("NBA",     nba, "nba")

    return f"""Tu es un analyste de performance pour un système de prédiction sportive.
Rédige un résumé concis (5-8 phrases) en français des performances du jour.
Compare le Brier score à la baseline indiquée (modèle aléatoire uniforme).
N'invente pas de données : si un sport n'a pas de données, dis-le et n'émets aucune conclusion le concernant.

{l1_block}

{nba_block}

Résumé :"""


def generate_summary(
    ollama_url: str,
    model: str,
    metrics: dict,
    timeout: float = 120.0,
) -> str:
    """Appelle Ollama pour générer le résumé. Retourne le texte généré.

    Lève une exception si l'appel échoue (le caller ne sauvegarde pas d'erreur comme résumé).
    """
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
    resp = httpx.post(
        f"{ollama_url}/api/generate",
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    text = resp.json().get("response", "").strip()
    log.info("ollama.generate_done", chars=len(text))
    return text
