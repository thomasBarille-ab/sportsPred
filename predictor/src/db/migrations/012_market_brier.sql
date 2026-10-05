-- Brier score des probabilités implicites du marché (après de-juicing)
-- NULL si aucune cote disponible pour ce match
ALTER TABLE prediction_scores ADD COLUMN IF NOT EXISTS market_brier FLOAT;
