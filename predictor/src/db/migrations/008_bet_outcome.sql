-- Colonne manquante pour savoir sur quel outcome le pari a été placé
ALTER TABLE bet_simulations ADD COLUMN IF NOT EXISTS bet_outcome VARCHAR(10);
