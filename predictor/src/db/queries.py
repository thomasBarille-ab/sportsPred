"""Requêtes SQL partagées entre claude_agent.py, trigger_server.py et mcp_server.py.

Chaque fonction prend des paramètres métier et retourne list[dict] via session.fetch_all.
"""

from __future__ import annotations

from . import session


def q_recent_predictions(sport: str, n: int = 20) -> list[dict]:
    n = min(max(n, 1), 30)
    rows = session.fetch_all(
        """
        SELECT f.home_team_name, f.away_team_name, f.match_date, f.round,
               p.predicted_outcome,
               ROUND(p.prob_home_win::numeric, 2) AS p_home,
               ROUND(p.prob_draw::numeric, 2)     AS p_draw,
               ROUND(p.prob_away_win::numeric, 2) AS p_away,
               p.explanation,
               r.actual_outcome,
               ps.is_correct,
               ROUND(ps.brier_score::numeric, 4)  AS brier_score,
               p.features_snapshot
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        LEFT JOIN prediction_scores ps ON ps.prediction_id = p.id
        LEFT JOIN results r ON r.fixture_id = p.fixture_id
        WHERE f.sport = %s
        ORDER BY f.match_date DESC
        LIMIT %s
        """,
        (sport, n),
    )
    return [dict(r) for r in rows]


def q_failure_patterns(sport: str) -> list[dict]:
    """Compare features moyennes entre prédictions correctes et incorrectes."""
    base = session.fetch_all(
        """
        SELECT
            ps.is_correct,
            COUNT(*)                                                              AS n,
            ROUND(AVG((p.features_snapshot->>'elo_diff')::float)::numeric, 1)    AS avg_elo_diff,
            ROUND(AVG(p.prob_home_win)::numeric, 3)                              AS avg_conf_home,
            ROUND(AVG(ps.brier_score)::numeric, 4)                               AS avg_brier
        FROM predictions p
        JOIN prediction_scores ps ON ps.prediction_id = p.id
        JOIN fixtures f ON f.id = p.fixture_id
        WHERE f.sport = %s AND p.features_snapshot IS NOT NULL
        GROUP BY ps.is_correct
        """,
        (sport,),
    )
    base_map = {r["is_correct"]: dict(r) for r in base}

    if sport in ("ligue1", "france_nt"):
        extra = session.fetch_all(
            """
            SELECT
                ps.is_correct,
                ROUND(AVG((p.features_snapshot->>'home_form_pts_last5')::float)::numeric, 2) AS avg_home_form,
                ROUND(AVG((p.features_snapshot->>'away_form_pts_last5')::float)::numeric, 2) AS avg_away_form,
                ROUND(AVG((p.features_snapshot->>'dc_p_home')::float)::numeric, 3)           AS avg_dc_p_home,
                ROUND(AVG((p.features_snapshot->>'implied_prob_home')::float)::numeric, 3)   AS avg_implied_home
            FROM predictions p
            JOIN prediction_scores ps ON ps.prediction_id = p.id
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE f.sport = %s AND p.features_snapshot IS NOT NULL
            GROUP BY ps.is_correct
            """,
            (sport,),
        )
    else:
        extra = session.fetch_all(
            """
            SELECT
                ps.is_correct,
                ROUND(AVG((p.features_snapshot->>'home_win_rate_last10')::float)::numeric, 3) AS avg_home_wr,
                ROUND(AVG((p.features_snapshot->>'away_win_rate_last10')::float)::numeric, 3) AS avg_away_wr,
                ROUND(AVG((p.features_snapshot->>'home_b2b')::float)::numeric, 3)             AS avg_home_b2b,
                ROUND(AVG((p.features_snapshot->>'away_b2b')::float)::numeric, 3)             AS avg_away_b2b
            FROM predictions p
            JOIN prediction_scores ps ON ps.prediction_id = p.id
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE f.sport = %s AND p.features_snapshot IS NOT NULL
            GROUP BY ps.is_correct
            """,
            (sport,),
        )

    extra_map = {r["is_correct"]: dict(r) for r in extra}
    result = []
    for is_correct, row in base_map.items():
        row.update(extra_map.get(is_correct, {}))
        result.append(row)
    return result


def q_model_performance_trend(sport: str, days: int = 30) -> list[dict]:
    days = min(max(days, 7), 365)
    rows = session.fetch_all(
        f"""
        SELECT DATE_TRUNC('week', f.match_date)                     AS week,
               COUNT(ps.id)                                          AS n,
               ROUND(AVG(ps.brier_score)::numeric, 4)                AS avg_brier,
               ROUND(AVG(ps.is_correct::int::float)::numeric, 3)    AS accuracy
        FROM prediction_scores ps
        JOIN fixtures f ON f.id = ps.fixture_id
        WHERE f.sport = %s
          AND f.match_date >= NOW() - INTERVAL '{days} days'
        GROUP BY 1
        ORDER BY 1
        """,
        (sport,),
    )
    return [dict(r) for r in rows]


def q_upcoming_fixtures(sport: str, days: int = 7) -> list[dict]:
    days = min(max(days, 1), 30)
    rows = session.fetch_all(
        """
        SELECT f.home_team_name, f.away_team_name, f.match_date, f.round,
               p.predicted_outcome,
               ROUND(p.prob_home_win::numeric, 2) AS p_home,
               ROUND(p.prob_draw::numeric, 2)     AS p_draw,
               ROUND(p.prob_away_win::numeric, 2) AS p_away,
               p.explanation,
               mo.odds_home, mo.odds_draw, mo.odds_away, mo.bookmaker,
               bs.ev_pct, bs.bet_outcome AS recommended_bet
        FROM fixtures f
        LEFT JOIN predictions p ON p.fixture_id = f.id
        LEFT JOIN LATERAL (
            SELECT odds_home, odds_draw, odds_away, bookmaker
            FROM match_odds WHERE fixture_id = f.id AND closing = FALSE
            ORDER BY CASE WHEN bookmaker = 'pinnacle' THEN 0 ELSE 1 END, fetched_at DESC
            LIMIT 1
        ) mo ON true
        LEFT JOIN bet_simulations bs ON bs.fixture_id = f.id
        WHERE f.sport = %s
          AND f.status IN ('SCHEDULED', 'TIMED')
          AND f.match_date BETWEEN NOW() AND NOW() + (%s || ' days')::interval
        ORDER BY f.match_date
        """,
        (sport, days),
    )
    return [dict(r) for r in rows]
