"""Capture des logs structlog dans un buffer thread-local pendant l'exécution d'un job."""

from __future__ import annotations

import threading
from typing import Any

_local = threading.local()


class StepCaptureProcessor:
    """Processor structlog — enregistre chaque event dans _local.steps si la capture est active."""

    def __call__(self, logger: Any, method: str, event_dict: dict) -> dict:
        if getattr(_local, "active", False):
            step: dict = {
                "ts": event_dict.get("timestamp", ""),
                "level": event_dict.get("level", method),
                "event": event_dict.get("event", ""),
            }
            for k, v in event_dict.items():
                if k not in ("timestamp", "level", "event", "_logger", "_record", "logger"):
                    step[k] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
            _local.steps.append(step)
        return event_dict


capture_processor = StepCaptureProcessor()


class capture_steps:
    """Context manager — active la capture des logs structlog dans une liste."""

    def __enter__(self) -> list:
        _local.active = True
        _local.steps = []
        return _local.steps

    def __exit__(self, *_: Any) -> None:
        _local.active = False
