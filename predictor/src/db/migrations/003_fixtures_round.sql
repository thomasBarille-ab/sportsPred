-- Ajout de la colonne round (journée) sur les fixtures Ligue 1
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS round INTEGER;

CREATE INDEX IF NOT EXISTS idx_fixtures_sport_round ON fixtures (sport, round);
