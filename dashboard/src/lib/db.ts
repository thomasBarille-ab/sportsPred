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
      ROUND(ps.brier_score::numeric, 4)  AS brier_score
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

export async function getLatestSummary() {
  const rows = await sql`
    SELECT content, summary_date, generated_at
    FROM daily_summaries
    ORDER BY summary_date DESC
    LIMIT 1
  `;
  return rows[0] ?? null;
}
