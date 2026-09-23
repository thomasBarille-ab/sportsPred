import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    postgres_url: str
    football_data_api_key: str
    balldontlie_api_key: str
    ollama_url: str
    ollama_model: str
    model_storage_path: str
    ingestion_hour_utc: int
    retrain_weekday: str   # "mon" | "tue" | ...
    retrain_hour_utc: int
    log_level: str
    predict_horizon_hours: int  # horizon de prédiction en heures (défaut 36)
    internal_api_token: str
    anthropic_api_key: str
    odds_api_key: str


def _validate_hour(key: str, value: int) -> int:
    if not (0 <= value <= 23):
        raise RuntimeError(f"Env var {key!r} must be between 0 and 23, got {value}")
    return value


def load_settings() -> Settings:
    ingestion_hour = int(os.environ.get("INGESTION_HOUR_UTC", "6"))
    retrain_hour   = int(os.environ.get("RETRAIN_HOUR_UTC", "3"))
    _validate_hour("INGESTION_HOUR_UTC", ingestion_hour)
    _validate_hour("RETRAIN_HOUR_UTC", retrain_hour)

    return Settings(
        postgres_url=_require("POSTGRES_URL"),
        football_data_api_key=_require("FOOTBALL_DATA_API_KEY"),
        balldontlie_api_key=os.environ.get("BALLDONTLIE_API_KEY", ""),
        ollama_url=os.environ.get("OLLAMA_URL", "http://ollama:11434"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.2:3b"),
        model_storage_path=os.environ.get("MODEL_STORAGE_PATH", "/models"),
        ingestion_hour_utc=ingestion_hour,
        retrain_weekday=os.environ.get("RETRAIN_WEEKDAY", "mon"),
        retrain_hour_utc=retrain_hour,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        predict_horizon_hours=int(os.environ.get("PREDICT_HORIZON_HOURS", "36")),
        internal_api_token=os.environ.get("INTERNAL_API_TOKEN", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        odds_api_key=os.environ.get("ODDS_API_KEY", ""),
    )


def _require(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"Required env var {key!r} is not set")
    return val
