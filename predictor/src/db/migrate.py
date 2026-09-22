"""Applique automatiquement les migrations SQL au démarrage.

Chaque fichier *.sql dans le dossier migrations/ est exécuté une seule fois,
dans l'ordre alphabétique. Un enregistrement est inséré dans schema_migrations
après chaque application réussie — les fichiers déjà joués sont ignorés.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

from . import session

log = structlog.get_logger()

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_migrations() -> None:
    _ensure_migrations_table()

    applied = {
        row["filename"]
        for row in session.fetch_all("SELECT filename FROM schema_migrations")
    }

    pending = sorted(
        f for f in _MIGRATIONS_DIR.glob("*.sql") if f.name not in applied
    )

    if not pending:
        log.info("migrate.up_to_date")
        return

    for path in pending:
        sql = path.read_text(encoding="utf-8")
        log.info("migrate.applying", file=path.name)
        try:
            session.execute(sql)
            session.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)",
                (path.name,),
            )
            log.info("migrate.applied", file=path.name)
        except Exception as exc:
            log.error("migrate.failed", file=path.name, error=str(exc))
            raise


def _ensure_migrations_table() -> None:
    session.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    VARCHAR(255) PRIMARY KEY,
            applied_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
        """
    )
