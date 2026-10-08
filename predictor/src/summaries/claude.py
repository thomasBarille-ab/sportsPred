"""Résumé quotidien des performances via Claude Haiku."""

from __future__ import annotations

import structlog

from ..llm_client import create_message
from .ollama import build_prompt

log = structlog.get_logger()

_MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 500


def generate_summary(api_key: str, metrics: dict) -> str:
    """Appelle Claude pour générer le résumé. Lève une exception si l'appel échoue."""
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY non configurée")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_prompt(metrics)

    log.info("claude_summary.generate_start", model=_MODEL)
    response = create_message(
        client,
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    log.info("claude_summary.generate_done", chars=len(text))
    return text
