-- Support de markets étendus (O/U total goals, BTTS) pour Ligue 1.
-- h2h reste dans match_odds + predictions comme avant.
-- Les nouveaux markets vivent dans match_odds_markets + predictions_markets.
-- bet_simulations gère tous les markets dans la même table.

-- ─── 1. Cotes multi-markets ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS match_odds_markets (
    id          BIGSERIAL PRIMARY KEY,
    fixture_id  BIGINT       NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE,
    bookmaker   VARCHAR(50)  NOT NULL,
    market      VARCHAR(30)  NOT NULL,                  -- 'ou_total', 'btts'
    selection   VARCHAR(30)  NOT NULL,                  -- 'over','under','yes','no'
    line        NUMERIC(5,2) NOT NULL DEFAULT 0,        -- 2.5 pour O/U, 0 pour BTTS
    odds        DOUBLE PRECISION NOT NULL,
    fetched_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closing     BOOLEAN      NOT NULL DEFAULT FALSE,
    source      VARCHAR(30)  NOT NULL DEFAULT 'odds_api',
    UNIQUE (fixture_id, bookmaker, market, selection, line, closing)
);

CREATE INDEX IF NOT EXISTS idx_match_odds_markets_fixture ON match_odds_markets (fixture_id);
CREATE INDEX IF NOT EXISTS idx_match_odds_markets_market  ON match_odds_markets (market);

-- ─── 2. Prédictions multi-markets (dérivées de Dixon-Coles) ──────────────────
CREATE TABLE IF NOT EXISTS predictions_markets (
    id              BIGSERIAL PRIMARY KEY,
    fixture_id      BIGINT       NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE,
    market          VARCHAR(30)  NOT NULL,
    selection       VARCHAR(30)  NOT NULL,
    line            NUMERIC(5,2) NOT NULL DEFAULT 0,
    prob            DOUBLE PRECISION NOT NULL CHECK (prob BETWEEN 0 AND 1),
    model_source    VARCHAR(50),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (fixture_id, market, selection, line)
);

CREATE INDEX IF NOT EXISTS idx_predictions_markets_fixture ON predictions_markets (fixture_id);
CREATE INDEX IF NOT EXISTS idx_predictions_markets_market  ON predictions_markets (market);

-- ─── 3. Généralise bet_simulations : nouveaux champs selection + line ───────
ALTER TABLE bet_simulations
  ADD COLUMN IF NOT EXISTS selection VARCHAR(30),
  ADD COLUMN IF NOT EXISTS line      NUMERIC(5,2);

-- Backfill selection depuis bet_outcome (paris h2h existants)
UPDATE bet_simulations
   SET selection = bet_outcome
 WHERE selection IS NULL AND bet_outcome IS NOT NULL;

-- Backfill line pour paris h2h existants
UPDATE bet_simulations SET line = 0 WHERE line IS NULL;
ALTER TABLE bet_simulations ALTER COLUMN line SET DEFAULT 0;
ALTER TABLE bet_simulations ALTER COLUMN line SET NOT NULL;

-- 4. Les nouveaux markets ne pointent pas vers predictions.id → NULL autorisé
ALTER TABLE bet_simulations ALTER COLUMN prediction_id DROP NOT NULL;

-- 5. Un match peut maintenant générer plusieurs paris (un par (market, selection, line))
ALTER TABLE bet_simulations DROP CONSTRAINT IF EXISTS bet_simulations_fixture_id_key;

CREATE UNIQUE INDEX IF NOT EXISTS bet_simulations_fixture_market_selection_line_uidx
  ON bet_simulations (fixture_id, market, selection, line);
