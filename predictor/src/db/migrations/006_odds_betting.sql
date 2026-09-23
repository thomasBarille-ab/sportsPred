-- Cotes bookmaker par match (ingérées via The Odds API ou CSV football-data.co.uk)
CREATE TABLE IF NOT EXISTS match_odds (
    id          BIGSERIAL PRIMARY KEY,
    fixture_id  BIGINT       NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE,
    bookmaker   VARCHAR(50)  NOT NULL,
    market      VARCHAR(20)  NOT NULL DEFAULT 'h2h',
    odds_home   DOUBLE PRECISION NOT NULL,
    odds_draw   DOUBLE PRECISION,          -- NULL pour NBA (2 issues)
    odds_away   DOUBLE PRECISION NOT NULL,
    fetched_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    source      VARCHAR(30)  NOT NULL DEFAULT 'odds_api',
    UNIQUE (fixture_id, bookmaker, market)
);

CREATE INDEX IF NOT EXISTS idx_match_odds_fixture ON match_odds (fixture_id);

-- Simulations de paris avec EV positif (peuplé par le job bet_simulation, Sprint 4)
CREATE TABLE IF NOT EXISTS bet_simulations (
    id              BIGSERIAL PRIMARY KEY,
    fixture_id      BIGINT       NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE,
    prediction_id   BIGINT       NOT NULL REFERENCES predictions(id),
    bookmaker       VARCHAR(50)  NOT NULL,
    market          VARCHAR(20)  NOT NULL DEFAULT 'h2h',
    odds_taken      DOUBLE PRECISION NOT NULL,
    ev_pct          DOUBLE PRECISION NOT NULL,
    stake_units     DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','won','lost','void')),
    pnl_units       DOUBLE PRECISION,
    settled_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (fixture_id)
);

CREATE INDEX IF NOT EXISTS idx_bet_simulations_status   ON bet_simulations (status);
CREATE INDEX IF NOT EXISTS idx_bet_simulations_fixture  ON bet_simulations (fixture_id);
