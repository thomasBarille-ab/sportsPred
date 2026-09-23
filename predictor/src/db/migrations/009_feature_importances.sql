-- Feature importances par version de modèle
CREATE TABLE IF NOT EXISTS feature_importances (
  id              BIGSERIAL PRIMARY KEY,
  model_version_id BIGINT NOT NULL REFERENCES model_versions(id) ON DELETE CASCADE,
  sport           VARCHAR(20) NOT NULL,
  feature_name    VARCHAR(100) NOT NULL,
  importance      FLOAT NOT NULL,
  recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feature_importances_model ON feature_importances(model_version_id);
CREATE INDEX IF NOT EXISTS idx_feature_importances_sport ON feature_importances(sport, recorded_at DESC);
