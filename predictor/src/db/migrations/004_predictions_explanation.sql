-- Ajout de la colonne explanation sur les prédictions
-- Générée par Claude Haiku au moment du predict, stockée définitivement
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS explanation TEXT;
