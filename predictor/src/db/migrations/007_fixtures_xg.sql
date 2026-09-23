-- xG (expected goals) par match, fournis par Understat (Ligue 1 uniquement)
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS home_xg NUMERIC(5, 2);
ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS away_xg NUMERIC(5, 2);
