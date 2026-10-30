-- Prédictions de l'agent LLM (Ollama) — indépendant du modèle ML
CREATE TABLE IF NOT EXISTS llm_agent_predictions (
  id                SERIAL PRIMARY KEY,
  fixture_id        INTEGER NOT NULL REFERENCES fixtures(id),
  sport             VARCHAR(20) NOT NULL,
  predicted_outcome VARCHAR(10) NOT NULL,
  confidence        SMALLINT NOT NULL CHECK (confidence BETWEEN 0 AND 100),
  reasoning         TEXT NOT NULL,
  search_snippets   JSONB,
  model_name        VARCHAR(50) NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (fixture_id)
);
