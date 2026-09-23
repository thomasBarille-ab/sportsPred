"""Job d'analyse post-match par l'agent Claude.

Tourne après evaluate (H+3). Interroge la DB via tool use et stocke
un rapport structuré dans daily_summaries.agent_report.
"""

from __future__ import annotations

import json
from datetime import date

import structlog

from ..db import session
from ..summaries.claude_agent import run_agent_analysis

log = structlog.get_logger()


def run_agent_analysis_job(api_key: str) -> int:
    """Lance l'agent et persiste le rapport. Retourne 1 si succès, 0 sinon."""
    report = run_agent_analysis(api_key)
    if report is None:
        return 0

    today = date.today()
    session.execute(
        """
        INSERT INTO daily_summaries (summary_date, content, agent_report)
        VALUES (%s, '', %s)
        ON CONFLICT (summary_date)
        DO UPDATE SET agent_report = EXCLUDED.agent_report
        """,
        (today, json.dumps(report)),
    )
    log.info("agent_analysis.saved", date=str(today))
    return 1
