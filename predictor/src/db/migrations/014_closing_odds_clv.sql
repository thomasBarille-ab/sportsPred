-- Cotes de clôture (H-1h avant le match) pour calcul du CLV
-- La contrainte unique intègre le flag closing pour séparer ouverture et clôture

-- 1. Ajouter la colonne closing avec valeur par défaut FALSE (cotes d'ouverture)
ALTER TABLE match_odds ADD COLUMN IF NOT EXISTS closing BOOLEAN NOT NULL DEFAULT FALSE;

-- 2. Supprimer l'ancienne contrainte (fixture_id, bookmaker, market)
ALTER TABLE match_odds
  DROP CONSTRAINT IF EXISTS match_odds_fixture_id_bookmaker_market_key;

-- 3. Recréer avec closing inclus
CREATE UNIQUE INDEX IF NOT EXISTS match_odds_fixture_bookmaker_market_closing_uidx
  ON match_odds (fixture_id, bookmaker, market, closing);

-- 4. CLV sur bet_simulations : (cote_prise / cote_clôture) - 1
ALTER TABLE bet_simulations ADD COLUMN IF NOT EXISTS clv FLOAT;
