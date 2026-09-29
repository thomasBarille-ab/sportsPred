-- Context pré-match généré par l'agent (météo + blessures/rotations LLM)
CREATE TABLE IF NOT EXISTS match_context (
    id                   SERIAL PRIMARY KEY,
    fixture_id           INTEGER NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE,
    agent_run_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    weather_temp_celsius FLOAT,
    weather_rain_mm      FLOAT,
    weather_wind_kmh     FLOAT,
    home_injuries_count  INTEGER NOT NULL DEFAULT 0,
    away_injuries_count  INTEGER NOT NULL DEFAULT 0,
    home_rotation_signal BOOLEAN NOT NULL DEFAULT FALSE,
    away_rotation_signal BOOLEAN NOT NULL DEFAULT FALSE,
    news_raw             JSONB,
    llm_analysis         JSONB,
    UNIQUE(fixture_id)
);

CREATE INDEX IF NOT EXISTS idx_match_context_fixture_id ON match_context(fixture_id);
