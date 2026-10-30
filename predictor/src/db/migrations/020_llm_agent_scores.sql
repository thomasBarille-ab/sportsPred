-- Scores post-match pour les prédictions de l'agent LLM
CREATE TABLE IF NOT EXISTS llm_agent_scores (
  id                    SERIAL PRIMARY KEY,
  agent_prediction_id   INTEGER NOT NULL REFERENCES llm_agent_predictions(id),
  fixture_id            INTEGER NOT NULL REFERENCES fixtures(id),
  actual_outcome        VARCHAR(10),
  is_correct            BOOLEAN,
  brier_score           NUMERIC(8,6),
  scored_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (fixture_id)
);
