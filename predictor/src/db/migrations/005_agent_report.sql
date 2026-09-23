-- Ajout du rapport structuré de l'agent Claude dans daily_summaries
ALTER TABLE daily_summaries ADD COLUMN IF NOT EXISTS agent_report JSONB;
