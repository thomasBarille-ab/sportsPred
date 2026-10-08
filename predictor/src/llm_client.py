"""Wrapper autour de anthropic.messages.create() avec suivi automatique des coûts et latences.

Usage :
    from .llm_client import create_message, reset_metrics, get_metrics

    reset_metrics()
    response = create_message(client, model=..., max_tokens=..., messages=...)
    stats = get_metrics()  # {"tokens_input": int, "tokens_output": int, "cost_usd": float, "latency_ms": int}
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import structlog

log = structlog.get_logger()

_WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")

# Tarification (USD / 1M tokens) — à mettre à jour si Anthropic change ses prix
_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5":           (0.80,  4.00),
    "claude-haiku-4-5-20251001":  (0.80,  4.00),
    "claude-sonnet-4-6":          (3.00, 15.00),
    "claude-opus-4-7":            (15.0, 75.00),
}

_tl = threading.local()


def reset_metrics() -> None:
    """Réinitialise les compteurs pour le job courant (thread-local)."""
    _tl.tokens_input  = 0
    _tl.tokens_output = 0
    _tl.cost_usd      = 0.0
    _tl.latency_ms    = 0


def get_metrics() -> dict:
    """Retourne les métriques accumulées depuis le dernier reset_metrics()."""
    return {
        "tokens_input":  getattr(_tl, "tokens_input",  0),
        "tokens_output": getattr(_tl, "tokens_output", 0),
        "cost_usd":      getattr(_tl, "cost_usd",      0.0),
        "latency_ms":    getattr(_tl, "latency_ms",    0),
    }


def _accumulate(model: str, usage: Any, latency_ms: int) -> None:
    """Met à jour les compteurs thread-local à partir des champs usage de la réponse."""
    input_tokens  = getattr(usage, "input_tokens",  0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0

    price_in, price_out = _PRICING.get(model, (3.0, 15.0))
    cost = (input_tokens * price_in + output_tokens * price_out) / 1_000_000

    _tl.tokens_input  = getattr(_tl, "tokens_input",  0) + input_tokens
    _tl.tokens_output = getattr(_tl, "tokens_output", 0) + output_tokens
    _tl.cost_usd      = getattr(_tl, "cost_usd",      0.0) + cost
    _tl.latency_ms    = getattr(_tl, "latency_ms",    0) + latency_ms


def create_message(client: Any, **kwargs: Any) -> Any:
    """Proxy autour de client.messages.create() qui accumule tokens, coût et latence.

    Tous les kwargs sont passés directement à client.messages.create().
    """
    model = kwargs.get("model", "unknown")
    if _WORKSPACE_ID:
        extra = kwargs.setdefault("extra_headers", {})
        extra.setdefault("anthropic-workspace-id", _WORKSPACE_ID)
    t0 = time.monotonic()
    response = client.messages.create(**kwargs)
    latency_ms = int((time.monotonic() - t0) * 1000)

    if hasattr(response, "usage"):
        _accumulate(model, response.usage, latency_ms)
        log.debug(
            "llm.call",
            model=model,
            tokens_in=getattr(response.usage, "input_tokens", 0),
            tokens_out=getattr(response.usage, "output_tokens", 0),
            latency_ms=latency_ms,
        )

    return response
