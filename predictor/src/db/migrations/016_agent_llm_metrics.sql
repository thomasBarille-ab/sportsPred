-- Métriques de coût et latence pour chaque appel LLM dans les jobs agents
ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS llm_tokens_input  INT;
ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS llm_tokens_output INT;
ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS llm_cost_usd      NUMERIC(10,6);
ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS llm_latency_ms    INT;
