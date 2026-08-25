-- Index supplémentaire pour les requêtes dashboard fréquentes
CREATE INDEX IF NOT EXISTS idx_prediction_scores_scored_at
    ON prediction_scores (scored_at DESC);

CREATE INDEX IF NOT EXISTS idx_fixtures_sport_status_date
    ON fixtures (sport, status, match_date);

CREATE INDEX IF NOT EXISTS idx_daily_summaries_date
    ON daily_summaries (summary_date DESC);
