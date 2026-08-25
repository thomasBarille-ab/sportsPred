-- ─────────────────────────────────────────────────────────────────────────────
-- Sports Predictor — schéma initial
-- Exécuté automatiquement par Postgres au premier démarrage du container
-- ─────────────────────────────────────────────────────────────────────────────

-- Matchs (à venir + joués)
CREATE TABLE IF NOT EXISTS fixtures (
    id              BIGSERIAL PRIMARY KEY,
    external_id     VARCHAR(200) NOT NULL,
    sport           VARCHAR(20)  NOT NULL CHECK (sport IN ('ligue1','nba')),
    home_team_id    VARCHAR(200) NOT NULL,
    home_team_name  VARCHAR(200) NOT NULL,
    away_team_id    VARCHAR(200) NOT NULL,
    away_team_name  VARCHAR(200) NOT NULL,
    match_date      TIMESTAMPTZ  NOT NULL,
    season          VARCHAR(20)  NOT NULL,
    competition     VARCHAR(100) NOT NULL,
    status          VARCHAR(50)  NOT NULL DEFAULT 'scheduled',
    home_score      INTEGER,
    away_score      INTEGER,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (external_id, sport)
);

CREATE INDEX IF NOT EXISTS idx_fixtures_sport_date ON fixtures (sport, match_date);
CREATE INDEX IF NOT EXISTS idx_fixtures_status     ON fixtures (status);

-- Versions de modèles entraînés
CREATE TABLE IF NOT EXISTS model_versions (
    id                  BIGSERIAL PRIMARY KEY,
    sport               VARCHAR(20)  NOT NULL CHECK (sport IN ('ligue1','nba')),
    version             VARCHAR(50)  NOT NULL,
    trained_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    training_samples    INTEGER,
    holdout_samples     INTEGER,
    holdout_brier       DOUBLE PRECISION,
    holdout_logloss     DOUBLE PRECISION,
    holdout_accuracy    DOUBLE PRECISION,
    is_production       BOOLEAN      NOT NULL DEFAULT FALSE,
    model_path          VARCHAR(500),
    hyperparameters     JSONB,
    feature_names       JSONB,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (sport, version)
);

CREATE INDEX IF NOT EXISTS idx_model_versions_sport_prod ON model_versions (sport, is_production);

-- Prédictions (verrouillées — jamais modifiées après création)
CREATE TABLE IF NOT EXISTS predictions (
    id                  BIGSERIAL PRIMARY KEY,
    fixture_id          BIGINT       NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE,
    model_version_id    BIGINT       NOT NULL REFERENCES model_versions(id),
    predicted_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    prob_home_win       DOUBLE PRECISION NOT NULL CHECK (prob_home_win BETWEEN 0 AND 1),
    prob_draw           DOUBLE PRECISION          CHECK (prob_draw BETWEEN 0 AND 1),  -- NULL pour NBA
    prob_away_win       DOUBLE PRECISION NOT NULL CHECK (prob_away_win BETWEEN 0 AND 1),
    predicted_outcome   VARCHAR(10)  NOT NULL CHECK (predicted_outcome IN ('home','draw','away')),
    features_snapshot   JSONB,
    UNIQUE (fixture_id)  -- une seule prédiction par match (verrouillage)
);

CREATE INDEX IF NOT EXISTS idx_predictions_fixture ON predictions (fixture_id);
CREATE INDEX IF NOT EXISTS idx_predictions_model   ON predictions (model_version_id);

-- Résultats réels (enregistrés après le match)
CREATE TABLE IF NOT EXISTS results (
    id              BIGSERIAL PRIMARY KEY,
    fixture_id      BIGINT      NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE UNIQUE,
    home_score      INTEGER     NOT NULL,
    away_score      INTEGER     NOT NULL,
    actual_outcome  VARCHAR(10) NOT NULL CHECK (actual_outcome IN ('home','draw','away')),
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Métriques par prédiction (calculées après le match)
CREATE TABLE IF NOT EXISTS prediction_scores (
    id              BIGSERIAL PRIMARY KEY,
    prediction_id   BIGINT  NOT NULL REFERENCES predictions(id) ON DELETE CASCADE UNIQUE,
    fixture_id      BIGINT  NOT NULL REFERENCES fixtures(id),
    brier_score     DOUBLE PRECISION NOT NULL,
    log_loss        DOUBLE PRECISION NOT NULL,
    is_correct      BOOLEAN NOT NULL,
    scored_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scores_fixture ON prediction_scores (fixture_id);

-- Logs des jobs (ingestion, prédiction, évaluation, réentraînement)
CREATE TABLE IF NOT EXISTS agent_logs (
    id                  BIGSERIAL PRIMARY KEY,
    job_name            VARCHAR(100) NOT NULL,
    sport               VARCHAR(20),
    started_at          TIMESTAMPTZ  NOT NULL,
    finished_at         TIMESTAMPTZ,
    status              VARCHAR(20)  NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed')),
    duration_seconds    DOUBLE PRECISION,
    records_processed   INTEGER,
    error_message       TEXT,
    details             JSONB,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_logs_job     ON agent_logs (job_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_logs_status  ON agent_logs (status);

-- Résumés texte quotidiens générés par Llama
CREATE TABLE IF NOT EXISTS daily_summaries (
    id              BIGSERIAL PRIMARY KEY,
    summary_date    DATE        NOT NULL UNIQUE,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content         TEXT        NOT NULL,
    metrics_snapshot JSONB
);
