-- Liste nominative des joueurs absents issue de l'agent de contexte
-- Format : [{"name": "Prénom Nom", "reason": "blessure | suspension | rotation"}]
ALTER TABLE match_context ADD COLUMN IF NOT EXISTS home_absent_players JSONB;
ALTER TABLE match_context ADD COLUMN IF NOT EXISTS away_absent_players JSONB;
