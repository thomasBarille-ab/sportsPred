-- Variante du modèle : 'standard' (avec cotes) ou 'no_odds' (sans cotes)
-- Seul le variant 'standard' peut être promu en production
ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS variant VARCHAR NOT NULL DEFAULT 'standard';

CREATE INDEX IF NOT EXISTS idx_model_versions_variant
  ON model_versions (sport, variant, is_production);
