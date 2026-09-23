import postgres from "postgres";

// Connexion directe Postgres — le dashboard tourne dans le même réseau Docker
const connectionString = process.env.POSTGRES_URL!;

const sql = postgres(connectionString, {
  max: 5,
  idle_timeout: 30,
  transform: postgres.camel,   // snake_case → camelCase automatiquement
});

export default sql;

// ── Requêtes réutilisables ─────────────────────────────────────────────────

export async function getOverviewStats(sport: string, days = 30) {
  const rows = await sql`
    SELECT
      COUNT(ps.id)                                    AS total,
      ROUND(AVG(ps.brier_score)::numeric, 4)          AS avg_brier,
      ROUND(AVG(ps.log_loss)::numeric, 4)             AS avg_logloss,
      ROUND(AVG(ps.is_correct::int::float)::numeric, 3) AS accuracy
    FROM prediction_scores ps
    JOIN fixtures f ON f.id = ps.fixture_id
    WHERE f.sport = ${sport}
      AND f.match_date >= NOW() - (${days} || ' days')::interval
  `;
  return rows[0];
}

export async function getAccuracyOverTime(sport: string) {
  return sql`
    SELECT
      DATE_TRUNC('week', f.match_date)               AS week,
      COUNT(ps.id)                                    AS n,
      ROUND(AVG(ps.is_correct::int::float)::numeric, 3) AS accuracy,
      ROUND(AVG(ps.brier_score)::numeric, 4)          AS avg_brier
    FROM prediction_scores ps
    JOIN fixtures f ON f.id = ps.fixture_id
    WHERE f.sport = ${sport}
    GROUP BY 1
    ORDER BY 1
  `;
}

export async function getCalibrationData(sport: string) {
  return sql`
    WITH buckets AS (
      SELECT
        FLOOR(p.prob_home_win * 10) / 10            AS bucket,
        p.prob_home_win,
        (r.actual_outcome = 'home')::int::float     AS is_home
      FROM predictions p
      JOIN fixtures f ON f.id = p.fixture_id
      JOIN results r ON r.fixture_id = p.fixture_id
      JOIN prediction_scores ps ON ps.prediction_id = p.id
      WHERE f.sport = ${sport}
    )
    SELECT
      bucket,
      ROUND(AVG(prob_home_win)::numeric, 3) AS mean_predicted,
      ROUND(AVG(is_home)::numeric, 3)       AS actual_freq,
      COUNT(*)                               AS n
    FROM buckets
    GROUP BY bucket
    ORDER BY bucket
  `;
}

export async function getRecentPredictions(sport: string, limit = 20) {
  return sql`
    SELECT
      f.home_team_name,
      f.away_team_name,
      f.match_date,
      p.predicted_outcome,
      ROUND(p.prob_home_win::numeric, 2) AS prob_home,
      ROUND(p.prob_draw::numeric, 2)     AS prob_draw,
      ROUND(p.prob_away_win::numeric, 2) AS prob_away,
      r.actual_outcome,
      ps.is_correct,
      ROUND(ps.brier_score::numeric, 4)  AS brier_score,
      p.explanation
    FROM predictions p
    JOIN fixtures f ON f.id = p.fixture_id
    LEFT JOIN results r ON r.fixture_id = p.fixture_id
    LEFT JOIN prediction_scores ps ON ps.prediction_id = p.id
    WHERE f.sport = ${sport}
    ORDER BY f.match_date DESC
    LIMIT ${limit}
  `;
}

export async function getModelVersions(sport: string) {
  return sql`
    SELECT id, version, trained_at, training_samples, holdout_samples,
           ROUND(holdout_brier::numeric, 5)    AS holdout_brier,
           ROUND(holdout_logloss::numeric, 5)  AS holdout_logloss,
           ROUND(holdout_accuracy::numeric, 3) AS holdout_accuracy,
           is_production
    FROM model_versions
    WHERE sport = ${sport}
    ORDER BY trained_at DESC
    LIMIT 20
  `;
}

export async function getAgentLogs(limit = 50) {
  return sql`
    SELECT id, job_name, sport, started_at, finished_at,
           status, duration_seconds, records_processed, error_message, details
    FROM agent_logs
    ORDER BY started_at DESC
    LIMIT ${limit}
  `;
}

export async function getMatchdaySummary(sport: string) {
  return sql`
    SELECT
      f.round                                               AS matchday,
      MIN(f.match_date)                                     AS first_match,
      MAX(f.match_date)                                     AS last_match,
      COUNT(ps.id)                                          AS scored,
      COUNT(p.id)                                           AS total,
      SUM(ps.is_correct::int)                               AS correct,
      ROUND(AVG(ps.is_correct::int::float)::numeric, 3)    AS accuracy,
      ROUND(AVG(ps.brier_score)::numeric, 4)                AS avg_brier
    FROM fixtures f
    JOIN predictions p ON p.fixture_id = f.id
    LEFT JOIN prediction_scores ps ON ps.fixture_id = f.id
    WHERE f.sport = ${sport}
      AND f.round IS NOT NULL
    GROUP BY f.round
    ORDER BY f.round DESC
    LIMIT 38
  `;
}

export async function getBettingKPIs() {
  const rows = await sql`
    SELECT
      COUNT(*)                                                   AS total_bets,
      COUNT(*) FILTER (WHERE status = 'won')                     AS won,
      COUNT(*) FILTER (WHERE status = 'lost')                    AS lost,
      COUNT(*) FILTER (WHERE status = 'pending')                 AS pending,
      ROUND(SUM(pnl_units)::numeric, 2)                          AS total_pnl,
      ROUND(
        COUNT(*) FILTER (WHERE status = 'won')::float
        / NULLIF(COUNT(*) FILTER (WHERE status IN ('won','lost')), 0)
      ::numeric, 3)                                              AS win_rate,
      ROUND(
        SUM(pnl_units) / NULLIF(COUNT(*) FILTER (WHERE status IN ('won','lost')), 0)
      ::numeric, 4)                                              AS avg_pnl_per_bet,
      ROUND(AVG(ev_pct)::numeric, 4)                             AS avg_ev_pct
    FROM bet_simulations
  `;
  return rows[0] ?? null;
}

export async function getBankrollHistory() {
  return sql`
    SELECT
      DATE_TRUNC('day', settled_at)                         AS day,
      SUM(pnl_units) OVER (ORDER BY settled_at)             AS cumulative_pnl,
      pnl_units,
      status
    FROM bet_simulations
    WHERE status IN ('won', 'lost')
    ORDER BY settled_at
  `;
}

export async function getUpcomingValueBets() {
  return sql`
    SELECT
      bs.id,
      f.home_team_name,
      f.away_team_name,
      f.match_date,
      f.sport,
      bs.bet_outcome,
      bs.bookmaker,
      ROUND(bs.odds_taken::numeric, 2)   AS odds_taken,
      ROUND(bs.ev_pct::numeric, 4)       AS ev_pct,
      ROUND(p.prob_home_win::numeric, 2) AS prob_home,
      ROUND(p.prob_draw::numeric, 2)     AS prob_draw,
      ROUND(p.prob_away_win::numeric, 2) AS prob_away
    FROM bet_simulations bs
    JOIN fixtures f ON f.id = bs.fixture_id
    JOIN predictions p ON p.id = bs.prediction_id
    WHERE bs.status = 'pending'
    ORDER BY f.match_date ASC
    LIMIT 20
  `;
}

export async function getBetHistory(limit = 50) {
  return sql`
    SELECT
      f.home_team_name,
      f.away_team_name,
      f.match_date,
      f.sport,
      bs.bet_outcome,
      bs.bookmaker,
      ROUND(bs.odds_taken::numeric, 2)  AS odds_taken,
      ROUND(bs.ev_pct::numeric, 4)      AS ev_pct,
      bs.status,
      ROUND(bs.pnl_units::numeric, 2)   AS pnl_units,
      bs.settled_at
    FROM bet_simulations bs
    JOIN fixtures f ON f.id = bs.fixture_id
    WHERE bs.status IN ('won', 'lost')
    ORDER BY bs.settled_at DESC
    LIMIT ${limit}
  `;
}

export async function getLatestSummary() {
  const rows = await sql`
    SELECT content, summary_date, generated_at, agent_report
    FROM daily_summaries
    ORDER BY summary_date DESC
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function getFeatureImportances(sport: string) {
  return sql`
    SELECT fi.feature_name, fi.importance
    FROM feature_importances fi
    JOIN model_versions mv ON mv.id = fi.model_version_id
    WHERE fi.sport = ${sport}
      AND mv.is_production = true
    ORDER BY fi.importance DESC
    LIMIT 30
  `.catch(() => [] as { feature_name: string; importance: number }[]);
}
