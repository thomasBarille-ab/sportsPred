-- Rattrapage : recrée la contrainte unique match_odds avec la colonne closing
-- Nécessaire si la migration 014 n'a pas pu s'appliquer complètement

-- 1. Ajouter closing si absent
ALTER TABLE match_odds ADD COLUMN IF NOT EXISTS closing BOOLEAN NOT NULL DEFAULT FALSE;

-- 2. Dédupliquer : garder la ligne la plus récente par (fixture_id, bookmaker, market, closing)
DELETE FROM match_odds a
USING match_odds b
WHERE a.ctid < b.ctid
  AND a.fixture_id = b.fixture_id
  AND a.bookmaker  = b.bookmaker
  AND a.market     = b.market
  AND a.closing    = b.closing;

-- 3. Supprimer l'ancienne contrainte à 3 colonnes si elle existe encore
ALTER TABLE match_odds
  DROP CONSTRAINT IF EXISTS match_odds_fixture_id_bookmaker_market_key;

-- 4. Recréer l'index unique à 4 colonnes
DROP INDEX IF EXISTS match_odds_fixture_bookmaker_market_closing_uidx;
CREATE UNIQUE INDEX match_odds_fixture_bookmaker_market_closing_uidx
  ON match_odds (fixture_id, bookmaker, market, closing);
