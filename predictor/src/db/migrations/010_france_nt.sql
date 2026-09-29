-- ─────────────────────────────────────────────────────────────────────────────
-- 010 — Équipe de France NT : extension des contraintes + tables dédiées
-- ─────────────────────────────────────────────────────────────────────────────

-- Étend la contrainte sport pour accepter france_nt
ALTER TABLE fixtures DROP CONSTRAINT IF EXISTS fixtures_sport_check;
ALTER TABLE fixtures ADD CONSTRAINT fixtures_sport_check
  CHECK (sport IN ('ligue1', 'nba', 'france_nt'));

ALTER TABLE model_versions DROP CONSTRAINT IF EXISTS model_versions_sport_check;
ALTER TABLE model_versions ADD CONSTRAINT model_versions_sport_check
  CHECK (sport IN ('ligue1', 'nba', 'france_nt'));

-- Compositions officielles par match (API-Football)
CREATE TABLE IF NOT EXISTS france_nt_lineups (
    id              BIGSERIAL    PRIMARY KEY,
    fixture_id      BIGINT       NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE,
    player_id       VARCHAR(50)  NOT NULL,
    player_name     VARCHAR(200) NOT NULL,
    position        VARCHAR(50),
    jersey_number   INTEGER,
    is_starter      BOOLEAN      NOT NULL DEFAULT TRUE,
    france_is_home  BOOLEAN      NOT NULL DEFAULT TRUE,
    fetched_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (fixture_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_france_nt_lineups_fixture  ON france_nt_lineups (fixture_id);
CREATE INDEX IF NOT EXISTS idx_france_nt_lineups_starter  ON france_nt_lineups (fixture_id, is_starter);
