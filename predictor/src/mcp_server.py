"""MCP Server — expose les outils de prédiction sportive à Claude Desktop et autres clients MCP.

Usage (Claude Desktop config) :
{
  "mcpServers": {
    "sports-predictor": {
      "command": "python",
      "args": ["-m", "predictor.src.mcp_server"],
      "env": { "POSTGRES_URL": "postgresql://..." }
    }
  }
}
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from datetime import date, datetime

import psycopg2
import psycopg2.extras
import psycopg2.pool
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("sports-predictor")

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get("POSTGRES_URL", "")
        if not url:
            raise RuntimeError("POSTGRES_URL env var not set")
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 5, url)
    return _pool


def _query(sql: str, params=None) -> list[dict]:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        pool.putconn(conn)


def _serialize(obj) -> object:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


def _to_json(rows: list[dict]) -> str:
    cleaned = [{k: _serialize(v) for k, v in row.items()} for row in rows]
    return json.dumps(cleaned, ensure_ascii=False, indent=2)


@mcp.tool()
def get_recent_predictions(sport: str = "ligue1", n: int = 10) -> str:
    """Récupère les N dernières prédictions avec résultats et features snapshot."""
    rows = _query(
        """
        SELECT p.id, f.match_date, f.sport, f.home_team_id, f.away_team_id,
               f.home_score, f.away_score, f.status,
               p.prob_home_win, p.prob_draw, p.prob_away_win, p.predicted_outcome,
               ps.is_correct, ps.brier_score
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        LEFT JOIN prediction_scores ps ON ps.prediction_id = p.id
        WHERE f.sport = %s
        ORDER BY f.match_date DESC
        LIMIT %s
        """,
        (sport, n),
    )
    return _to_json(rows)


@mcp.tool()
def get_model_performance(sport: str = "ligue1", days: int = 30) -> str:
    """Retourne les métriques de performance sur les N derniers jours (Brier, accuracy)."""
    rows = _query(
        """
        SELECT DATE_TRUNC('week', f.match_date)::date AS week,
               COUNT(ps.id)                            AS n,
               ROUND(AVG(ps.brier_score)::numeric, 4) AS avg_brier,
               ROUND(AVG(ps.is_correct::int::float)::numeric, 3) AS accuracy
        FROM fixtures f
        JOIN predictions p  ON p.fixture_id = f.id
        JOIN prediction_scores ps ON ps.prediction_id = p.id
        WHERE f.sport = %s
          AND f.match_date >= NOW() - INTERVAL '1 day' * %s
        GROUP BY 1
        ORDER BY 1
        """,
        (sport, days),
    )
    return _to_json(rows)


@mcp.tool()
def get_failure_patterns(sport: str = "ligue1") -> str:
    """Compare les features moyennes entre prédictions correctes et incorrectes."""
    rows = _query(
        """
        SELECT ps.is_correct,
               COUNT(*)                                AS n,
               ROUND(AVG(ps.brier_score)::numeric, 4) AS avg_brier,
               ROUND(AVG(p.prob_home_win)::numeric, 3) AS avg_p_home,
               ROUND(AVG(p.prob_away_win)::numeric, 3) AS avg_p_away
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        JOIN prediction_scores ps ON ps.prediction_id = p.id
        WHERE f.sport = %s
          AND f.match_date >= NOW() - INTERVAL '60 days'
        GROUP BY ps.is_correct
        """,
        (sport,),
    )
    return _to_json(rows)


@mcp.tool()
def get_upcoming_fixtures(sport: str = "ligue1", hours: int = 48) -> str:
    """Liste les prochains matchs avec prédictions et cotes disponibles."""
    rows = _query(
        """
        SELECT f.id, f.match_date, f.home_team_id, f.away_team_id, f.status, f.round,
               p.prob_home_win, p.prob_draw, p.prob_away_win, p.predicted_outcome,
               mo.odds_home, mo.odds_draw, mo.odds_away, mo.bookmaker
        FROM fixtures f
        LEFT JOIN predictions p ON p.fixture_id = f.id
        LEFT JOIN LATERAL (
            SELECT odds_home, odds_draw, odds_away, bookmaker
            FROM match_odds
            WHERE fixture_id = f.id
            ORDER BY CASE WHEN bookmaker = 'pinnacle' THEN 0 ELSE 1 END, fetched_at DESC
            LIMIT 1
        ) mo ON true
        WHERE f.sport = %s
          AND f.status IN ('SCHEDULED', 'TIMED')
          AND f.match_date BETWEEN NOW() AND NOW() + INTERVAL '1 hour' * %s
        ORDER BY f.match_date
        """,
        (sport, hours),
    )
    return _to_json(rows)


@mcp.tool()
def get_value_bets(min_ev_pct: float = 0.05) -> str:
    """Retourne les value bets actives avec EV >= seuil (décimal, ex: 0.05 = 5%)."""
    rows = _query(
        """
        SELECT bs.fixture_id, f.sport, f.match_date, f.home_team_id, f.away_team_id,
               bs.bet_outcome, bs.odds_taken, bs.ev_pct, bs.bookmaker, bs.status
        FROM bet_simulations bs
        JOIN fixtures f ON f.id = bs.fixture_id
        WHERE bs.ev_pct >= %s
          AND bs.status = 'pending'
          AND f.match_date >= NOW()
        ORDER BY bs.ev_pct DESC
        LIMIT 20
        """,
        (min_ev_pct,),
    )
    return _to_json(rows)


@mcp.tool()
def get_betting_performance(sport: str = "ligue1", days: int = 90) -> str:
    """Résumé de la performance des simulations de paris (ROI, PnL)."""
    rows = _query(
        """
        SELECT f.sport,
               COUNT(*)                               AS total_bets,
               SUM(CASE WHEN bs.status = 'won'  THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN bs.status = 'lost' THEN 1 ELSE 0 END) AS losses,
               ROUND(SUM(bs.pnl_units)::numeric, 2)  AS total_pnl_units,
               ROUND(AVG(bs.ev_pct)::numeric, 4)     AS avg_ev_pct
        FROM bet_simulations bs
        JOIN fixtures f ON f.id = bs.fixture_id
        WHERE bs.status IN ('won', 'lost')
          AND f.sport = %s
          AND f.match_date >= NOW() - INTERVAL '1 day' * %s
        GROUP BY f.sport
        """,
        (sport, days),
    )
    return _to_json(rows)


@mcp.tool()
def get_feature_importances(sport: str = "ligue1") -> str:
    """Retourne les feature importances du modèle en production."""
    rows = _query(
        """
        SELECT fi.feature_name, ROUND(fi.importance::numeric, 5) AS importance
        FROM feature_importances fi
        JOIN model_versions mv ON mv.id = fi.model_version_id
        WHERE fi.sport = %s AND mv.is_production = true
        ORDER BY fi.importance DESC
        """,
        (sport,),
    )
    return _to_json(rows)


if __name__ == "__main__":
    mcp.run()
